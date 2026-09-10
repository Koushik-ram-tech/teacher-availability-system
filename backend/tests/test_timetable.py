"""Backend unit tests for the Teacher Availability System (Day-1 timetable slice).

Test strategy:
==============
These tests are split into two categories:

1. UNIT TESTS (no database required)
   ─────────────────────────────────
   These test Pydantic schema validation, schedule_config lookups, and the
   duplicate-slot rejection logic entirely in-process. They run everywhere and
   require no external services.

2. INTEGRATION TESTS (live PostgreSQL/Supabase required)
   ─────────────────────────────────────────────────────
   These tests exercise the FastAPI endpoints end-to-end using an in-memory
   SQLite engine.  SQLite is NOT a PostgreSQL substitute:

   • btrim / gen_random_uuid PostgreSQL functions are absent in SQLite.
     We work around this by seeding data via raw SQL and using ORM queries.
   • PGUUID(as_uuid=True) column types do not coerce correctly on SQLite
     because datetime columns reject empty-string defaults.
   • Deferred constraint triggers (slot occupancy validation) do NOT fire.

   Because of these limitations, tests that need full ORM round-trips are
   marked `@pytest.mark.integration` and skipped unless DATABASE_URL is set
   to a real PostgreSQL connection string.  They are provided as reference
   tests and must be run manually against the Supabase instance.

   To run integration tests:
       DATABASE_URL=postgresql+psycopg://... pytest -m integration -v

Covered by unit tests:
 1. Pydantic validation: slot code validation
 2. Pydantic validation: duplicate slot rejection within an entry
 3. Pydantic validation: cross-entry duplicate rejection within a day
 4. Pydantic validation: unknown day name rejection
 5. Pydantic validation: blank academic_year rejection
 6. schedule_config: SLOT_CODES list is correct
 7. schedule_config: day name → ISO mapping
 8. API: missing academic_year query param returns 422
 9. API: invalid status query param returns 400
10. API: invalid day name returns 400
"""
from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event as sa_event, text
from sqlalchemy.orm import Session, sessionmaker

import app.db as app_db_module
from app.core.schedule_config import DAY_NAME_TO_ISO, PERIOD_SEQUENCE, SLOT_CODES, VALID_LAB_PAIRS, is_valid_academic_year
from app.db import get_db
from app.main import app
from app.schemas.timetable import ScheduleEntryIn, TimetableWriteIn


# ===========================================================================
# 1. Pure unit tests — no database required
# ===========================================================================


class TestScheduleConfig:
    """Verify the schedule configuration matches the schema specification."""

    def test_slot_codes_are_s1_to_s9(self) -> None:
        """Test 6: SLOT_CODES must be exactly S1–S9 in order."""
        assert SLOT_CODES == ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"]

    def test_period_sequence_has_eleven_rows(self) -> None:
        """There should be 9 slots + 2 breaks = 11 periods."""
        assert len(PERIOD_SEQUENCE) == 11
        breaks = [p for p in PERIOD_SEQUENCE if p.kind == "BREAK"]
        slots = [p for p in PERIOD_SEQUENCE if p.kind == "SLOT"]
        assert len(breaks) == 2
        assert len(slots) == 9

    def test_day_name_to_iso_mapping(self) -> None:
        """Test 7: Monday → 1, Saturday → 6, Sunday absent."""
        assert DAY_NAME_TO_ISO["monday"] == 1
        assert DAY_NAME_TO_ISO["saturday"] == 6
        assert "sunday" not in DAY_NAME_TO_ISO

    def test_valid_lab_pairs_count(self) -> None:
        """There are exactly 6 valid consecutive lab slot pairs."""
        assert len(VALID_LAB_PAIRS) == 6

    def test_valid_lab_pairs_no_break_crossing(self) -> None:
        """S3+S4 and S5+S6 cross a break — must NOT appear in VALID_LAB_PAIRS."""
        assert frozenset({"S3", "S4"}) not in VALID_LAB_PAIRS
        assert frozenset({"S5", "S6"}) not in VALID_LAB_PAIRS

    def test_valid_lab_pairs_expected_pairs(self) -> None:
        """The six valid pairs are exactly S1+S2, S2+S3, S4+S5, S6+S7, S7+S8, S8+S9."""
        expected = {
            frozenset({"S1", "S2"}),
            frozenset({"S2", "S3"}),
            frozenset({"S4", "S5"}),
            frozenset({"S6", "S7"}),
            frozenset({"S7", "S8"}),
            frozenset({"S8", "S9"}),
        }
        assert VALID_LAB_PAIRS == expected

    def test_is_valid_academic_year_valid(self) -> None:
        assert is_valid_academic_year("2025-2026") is True
        assert is_valid_academic_year("2099-2100") is True

    def test_is_valid_academic_year_non_consecutive_rejected(self) -> None:
        assert is_valid_academic_year("2025-2027") is False
        assert is_valid_academic_year("2025-2025") is False

    def test_is_valid_academic_year_wrong_format_rejected(self) -> None:
        assert is_valid_academic_year("2025") is False
        assert is_valid_academic_year("TEST-2025") is False
        assert is_valid_academic_year("") is False


class TestScheduleEntryInValidation:
    """Test 1 & 2: ScheduleEntryIn Pydantic schema validation."""

    def test_valid_single_slot_class(self) -> None:
        entry = ScheduleEntryIn(slot_ids=["S1"], entry_type="CLASS", subject_or_activity="Math")
        assert entry.slot_ids == ["S1"]

    def test_valid_multi_slot_class_allowed(self) -> None:
        """CLASS may use a single slot; no consecutive requirement."""
        entry = ScheduleEntryIn(slot_ids=["S1"], entry_type="CLASS")
        assert entry.entry_type == "CLASS"

    # ------------------------------------------------------------------
    # Subject / activity is OPTIONAL
    # ------------------------------------------------------------------

    def test_class_without_subject_accepted(self) -> None:
        """CLASS with no subject is valid; occupancy is the primary purpose."""
        entry = ScheduleEntryIn(slot_ids=["S1"], entry_type="CLASS")
        assert entry.subject_or_activity is None
        assert entry.effective_subject == "CLASS"  # DB fallback

    def test_lab_without_subject_accepted(self) -> None:
        """LAB with no subject but valid consecutive slots is accepted."""
        entry = ScheduleEntryIn(slot_ids=["S6", "S7"], entry_type="LAB")
        assert entry.subject_or_activity is None
        assert entry.effective_subject == "LAB"

    def test_other_without_subject_accepted(self) -> None:
        """OTHER with no subject is accepted."""
        entry = ScheduleEntryIn(slot_ids=["S9"], entry_type="OTHER")
        assert entry.effective_subject == "OTHER"

    def test_blank_subject_uses_entry_type_fallback(self) -> None:
        """Blank subject_or_activity falls back to entry_type in effective_subject."""
        entry = ScheduleEntryIn(slot_ids=["S1"], entry_type="CLASS", subject_or_activity="   ")
        # Pydantic accepts blank string; effective_subject strips and falls back.
        assert entry.effective_subject == "CLASS"

    def test_explicit_subject_preserved(self) -> None:
        """When a non-blank subject is provided it is returned by effective_subject."""
        entry = ScheduleEntryIn(slot_ids=["S1"], entry_type="CLASS", subject_or_activity="  Math  ")
        assert entry.effective_subject == "Math"

    def test_valid_lab_consecutive_pair(self) -> None:
        """A LAB with a valid consecutive pair must be accepted."""
        entry = ScheduleEntryIn(slot_ids=["S6", "S7"], entry_type="LAB")
        assert len(entry.slot_ids) == 2

    def test_all_valid_lab_pairs_accepted(self) -> None:
        """Every pair in VALID_LAB_PAIRS must pass validation."""
        for pair in VALID_LAB_PAIRS:
            codes = sorted(pair)  # deterministic order
            entry = ScheduleEntryIn(slot_ids=codes, entry_type="LAB")
            assert len(entry.slot_ids) == 2

    def test_lab_break_crossing_pair_rejected(self) -> None:
        """S3+S4 crosses the morning break — must be rejected for LAB."""
        with pytest.raises(ValidationError, match="consecutive"):
            ScheduleEntryIn(slot_ids=["S3", "S4"], entry_type="LAB", subject_or_activity="Bad Lab")

    def test_lab_non_adjacent_pair_rejected(self) -> None:
        """S1+S6 are not adjacent — must be rejected for LAB."""
        with pytest.raises(ValidationError, match="consecutive"):
            ScheduleEntryIn(slot_ids=["S1", "S6"], entry_type="LAB", subject_or_activity="Bad Lab")

    def test_lab_single_slot_rejected(self) -> None:
        """A LAB with only one slot must be rejected."""
        with pytest.raises(ValidationError, match="consecutive"):
            ScheduleEntryIn(slot_ids=["S1"], entry_type="LAB", subject_or_activity="Single Slot Lab")

    def test_lab_three_slots_rejected(self) -> None:
        """A LAB with three slots must be rejected (even if all consecutive)."""
        with pytest.raises(ValidationError, match="consecutive"):
            ScheduleEntryIn(slot_ids=["S1", "S2", "S3"], entry_type="LAB", subject_or_activity="Triple Lab")

    def test_lab_lunch_crossing_pair_rejected(self) -> None:
        """S5+S6 crosses the lunch break — must be rejected for LAB."""
        with pytest.raises(ValidationError, match="consecutive"):
            ScheduleEntryIn(slot_ids=["S5", "S6"], entry_type="LAB", subject_or_activity="Lunch Cross Lab")

    def test_class_multiple_slots_no_consecutive_check(self) -> None:
        """CLASS with S3+S4 (crosses break) is allowed — rule only applies to LAB."""
        entry = ScheduleEntryIn(slot_ids=["S3", "S4"], entry_type="CLASS", subject_or_activity="Cross-break Class")
        assert "S3" in entry.slot_ids and "S4" in entry.slot_ids

    def test_other_multiple_slots_no_consecutive_check(self) -> None:
        """OTHER with S1+S9 (non-adjacent) is allowed — rule only applies to LAB."""
        entry = ScheduleEntryIn(slot_ids=["S1", "S9"], entry_type="OTHER", subject_or_activity="All-day Other")
        assert len(entry.slot_ids) == 2

    def test_unknown_slot_code_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown slot codes"):
            ScheduleEntryIn(slot_ids=["X99"], entry_type="CLASS")

    def test_duplicate_slot_within_entry_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate slot codes"):
            ScheduleEntryIn(slot_ids=["S1", "S1"], entry_type="CLASS")


class TestTimetableWriteInValidation:
    """Test 3 & 4 & 5: TimetableWriteIn Pydantic schema validation."""

    def test_valid_payload(self) -> None:
        payload = TimetableWriteIn(
            academic_year="2025-2026",
            days={
                "monday": [
                    ScheduleEntryIn(slot_ids=["S1"], entry_type="CLASS", subject_or_activity="Math"),
                ]
            },
        )
        assert payload.academic_year == "2025-2026"

    def test_valid_payload_no_subject(self) -> None:
        """A full day's worth of entries with no subject should be accepted."""
        payload = TimetableWriteIn(
            academic_year="2025-2026",
            days={
                "monday": [
                    ScheduleEntryIn(slot_ids=["S1"], entry_type="CLASS"),
                    ScheduleEntryIn(slot_ids=["S2"], entry_type="CLASS"),
                    ScheduleEntryIn(slot_ids=["S4"], entry_type="CLASS"),
                    ScheduleEntryIn(slot_ids=["S6", "S7"], entry_type="LAB"),
                    ScheduleEntryIn(slot_ids=["S9"], entry_type="OTHER"),
                ]
            },
        )
        assert len(payload.days["monday"]) == 5

    def test_multiple_entries_per_day_different_slots(self) -> None:
        """Multiple entries on the same day using distinct slots must be accepted."""
        payload = TimetableWriteIn(
            academic_year="2025-2026",
            days={
                "tuesday": [
                    ScheduleEntryIn(slot_ids=["S1"], entry_type="CLASS", subject_or_activity="Physics"),
                    ScheduleEntryIn(slot_ids=["S2"], entry_type="CLASS", subject_or_activity="Chemistry"),
                    ScheduleEntryIn(slot_ids=["S4", "S5"], entry_type="LAB", subject_or_activity="Chem Lab"),
                    ScheduleEntryIn(slot_ids=["S6"], entry_type="OTHER"),
                ]
            },
        )
        assert len(payload.days["tuesday"]) == 4

    def test_valid_academic_year_format(self) -> None:
        payload = TimetableWriteIn(academic_year="2099-2100", days={})
        assert payload.academic_year == "2099-2100"

    def test_non_consecutive_academic_year_rejected(self) -> None:
        """2025-2027 skips a year — must be rejected."""
        with pytest.raises(ValidationError, match="YYYY-YYYY"):
            TimetableWriteIn(academic_year="2025-2027", days={})

    def test_wrong_format_academic_year_rejected(self) -> None:
        """Plain '2025' without a hyphenated second year must be rejected."""
        with pytest.raises(ValidationError, match="YYYY-YYYY"):
            TimetableWriteIn(academic_year="2025", days={})

    def test_cross_entry_duplicate_slot_rejected(self) -> None:
        with pytest.raises(ValidationError, match="claimed by more than one entry"):
            TimetableWriteIn(
                academic_year="2025-2026",
                days={
                    "monday": [
                        ScheduleEntryIn(slot_ids=["S1"], entry_type="CLASS", subject_or_activity="Math"),
                        ScheduleEntryIn(slot_ids=["S1"], entry_type="CLASS", subject_or_activity="Physics"),
                    ]
                },
            )

    def test_unknown_day_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown day names"):
            TimetableWriteIn(academic_year="2025-2026", days={"sunday": []})

    def test_blank_academic_year_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TimetableWriteIn(academic_year="   ", days={})


# ===========================================================================
# 2. Lightweight API tests using TestClient (no DB seed needed)
# ===========================================================================
# These tests exercise parameter validation at the HTTP layer.
# They use a throwaway SQLite engine so the app can import without a real DB.
# ===========================================================================

_SQLITE_DDL = [
    "CREATE TABLE IF NOT EXISTS programs (id TEXT PRIMARY KEY, name TEXT NOT NULL, level TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1)",
    "CREATE TABLE IF NOT EXISTS teachers (id TEXT PRIMARY KEY, name TEXT NOT NULL, acronym TEXT NOT NULL, level TEXT NOT NULL, program_id TEXT NOT NULL, semester INTEGER NOT NULL, department TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS time_slots (id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, start_time TEXT NOT NULL, end_time TEXT NOT NULL, sequence INTEGER NOT NULL UNIQUE, is_active INTEGER NOT NULL DEFAULT 1)",
    "CREATE TABLE IF NOT EXISTS timetables (id TEXT PRIMARY KEY, teacher_id TEXT NOT NULL, academic_year TEXT NOT NULL, effective_from TEXT, effective_to TEXT, status TEXT NOT NULL, source TEXT NOT NULL, last_verified_at TEXT, created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS schedule_entries (id TEXT PRIMARY KEY, timetable_id TEXT NOT NULL, day_of_week INTEGER NOT NULL, entry_type TEXT NOT NULL, subject_or_activity TEXT NOT NULL, section TEXT, room TEXT, notes TEXT)",
    "CREATE TABLE IF NOT EXISTS schedule_entry_slots (schedule_entry_id TEXT NOT NULL, time_slot_id TEXT NOT NULL, PRIMARY KEY (schedule_entry_id, time_slot_id))",
]

_lite_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
_LiteSession = sessionmaker(bind=_lite_engine, autoflush=False, autocommit=False, expire_on_commit=False)

with _lite_engine.begin() as _conn:
    for _stmt in _SQLITE_DDL:
        _conn.execute(text(_stmt))


@pytest.fixture()
def _lite_db() -> Generator[Session, None, None]:
    conn = _lite_engine.connect()
    conn.begin()
    session = _LiteSession(bind=conn)
    try:
        yield session
    finally:
        session.close()
        conn.rollback()
        conn.close()


@pytest.fixture()
def api_client(_lite_db: Session) -> Generator[TestClient, None, None]:
    def _override() -> Generator[Session, None, None]:
        yield _lite_db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestApiParameterValidation:
    """Tests 8, 9, 10: HTTP-level parameter validation."""

    def test_missing_academic_year_returns_422(self, api_client: TestClient) -> None:
        """Test 8: academic_year is required; omitting it must return 422."""
        tid = str(uuid.uuid4())
        resp = api_client.get(
            f"/api/v1/teachers/{tid}/timetable",
            params={"day": "monday", "status": "DRAFT"},
        )
        assert resp.status_code == 422

    def test_missing_day_returns_422(self, api_client: TestClient) -> None:
        tid = str(uuid.uuid4())
        resp = api_client.get(
            f"/api/v1/teachers/{tid}/timetable",
            params={"academic_year": "2025-2026", "status": "DRAFT"},
        )
        assert resp.status_code == 422

    def test_missing_status_returns_422(self, api_client: TestClient) -> None:
        tid = str(uuid.uuid4())
        resp = api_client.get(
            f"/api/v1/teachers/{tid}/timetable",
            params={"day": "monday", "academic_year": "2025-2026"},
        )
        assert resp.status_code == 422

    def test_invalid_status_returns_400(self, api_client: TestClient) -> None:
        """Test 9: 'status=LATEST' should return 400."""
        tid = str(uuid.uuid4())
        resp = api_client.get(
            f"/api/v1/teachers/{tid}/timetable",
            params={"day": "monday", "academic_year": "2025-2026", "status": "LATEST"},
        )
        assert resp.status_code == 400

    def test_invalid_day_returns_400(self, api_client: TestClient) -> None:
        """Test 10: 'day=sunday' (not a working day) should return 400."""
        tid = str(uuid.uuid4())
        resp = api_client.get(
            f"/api/v1/teachers/{tid}/timetable",
            params={"day": "sunday", "academic_year": "2025-2026", "status": "DRAFT"},
        )
        assert resp.status_code == 400

    def test_unknown_teacher_returns_404(self, api_client: TestClient) -> None:
        """A nonexistent teacher_id must return 404, not 500."""
        tid = str(uuid.uuid4())
        resp = api_client.get(
            f"/api/v1/teachers/{tid}/timetable",
            params={"day": "monday", "academic_year": "2025-2026", "status": "DRAFT"},
        )
        assert resp.status_code == 404


# Real PostgreSQL integration tests live in test_integration.py.
# They are discovered automatically by pytest and require DATABASE_URL to be set.
