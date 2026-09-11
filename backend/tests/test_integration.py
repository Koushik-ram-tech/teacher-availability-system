"""Real PostgreSQL integration tests for the timetable API.

These tests run ONLY when the application's DATABASE_URL points at a live
PostgreSQL/Supabase database (read from backend/.env exactly as the app does).

Skip condition
--------------
The tests are automatically skipped when:
  - DATABASE_URL is not set in backend/.env, OR
  - pydantic_settings raises a ValidationError while loading Settings

Each test creates its own isolated, clearly-labeled test data and removes it
in a finally block.  No pre-existing records are modified or deleted.

Sentinel values used to identify test data
------------------------------------------
  - program: name = "TEST_INTEGRATION_PROGRAM_<unique_suffix>"
  - teacher: acronym starts with "ZZZ_"
  - academic_year: "TEST-<unique_suffix>"

These prefixes are never used in production data.

Trigger behavior under test (Scenario 4)
-----------------------------------------
The schema defines three DEFERRABLE INITIALLY DEFERRED constraint triggers:
  trg_schedule_entries_validate_occupancy  (ON schedule_entries)
  trg_schedule_entry_slots_validate_occupancy  (ON schedule_entry_slots)
  trg_timetable_validate_confirmation  (ON timetables)

All three call validate_timetable_slot_occupancy() at commit time.
The third trigger fires when timetables.status is set to 'CONFIRMED'.

Day-1 has no /confirm endpoint.  Scenario 4 exercises the trigger directly
via a raw SQL UPDATE on the timetables table, which is the only existing
database operation that can transition status to CONFIRMED.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

# ---------------------------------------------------------------------------
# Skip the whole module unless DATABASE_URL is available and is PostgreSQL.
# We read it the same way the application does: via pydantic_settings from
# backend/.env.  If Settings() raises, there is no live DB → skip.
# ---------------------------------------------------------------------------

_SKIP_REASON: str | None = None
_PG_DATABASE_URL: str | None = None

try:
    from pydantic import ValidationError as PydanticValidationError
    from app.core.config import get_settings

    _settings = get_settings()
    _raw_url = _settings.database_url

    # Normalize postgres:// → postgresql+psycopg://
    if _raw_url.startswith("postgres://"):
        _PG_DATABASE_URL = _raw_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif _raw_url.startswith("postgresql://"):
        _PG_DATABASE_URL = _raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    elif _raw_url.startswith("postgresql+psycopg://"):
        _PG_DATABASE_URL = _raw_url
    else:
        _SKIP_REASON = f"DATABASE_URL does not look like a PostgreSQL URL: {_raw_url[:30]}..."

except Exception as exc:  # noqa: BLE001
    _SKIP_REASON = f"Could not load DATABASE_URL from backend/.env: {exc}"


pytestmark = pytest.mark.integration

_requires_pg = pytest.mark.skipif(
    _SKIP_REASON is not None,
    reason=_SKIP_REASON or "PostgreSQL unavailable",
)


# ---------------------------------------------------------------------------
# PostgreSQL engine and session factory
# ---------------------------------------------------------------------------

def _make_pg_engine() -> sa.Engine:
    assert _PG_DATABASE_URL is not None
    return sa.create_engine(_PG_DATABASE_URL, pool_pre_ping=True, echo=False)


# ---------------------------------------------------------------------------
# Lookup helpers (read-only, never modify existing records)
# ---------------------------------------------------------------------------

def _get_active_program(db: Session) -> dict[str, Any]:
    """Return the first active PG program from the real database."""
    row = db.execute(
        sa.text("SELECT id, name, level FROM programs WHERE is_active = TRUE AND level = 'PG' LIMIT 1")
    ).mappings().first()
    if row is None:
        raise RuntimeError(
            "No active PG program found in the database. "
            "Run the seed SQL before running integration tests."
        )
    return dict(row)


def _get_slot_codes(db: Session) -> list[str]:
    """Return active slot codes ordered by sequence."""
    rows = db.execute(
        sa.text("SELECT code FROM time_slots WHERE is_active = TRUE ORDER BY sequence")
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Isolation helpers
# ---------------------------------------------------------------------------

def _unique_suffix() -> str:
    return uuid.uuid4().hex[:8].upper()


def _create_test_teacher(db: Session, program: dict[str, Any], suffix: str) -> dict[str, Any]:
    """Insert a clearly-labeled test teacher. Returns {'id': str, 'acronym': str}."""
    result = db.execute(
        sa.text(
            "INSERT INTO teachers (name, acronym, level, program_id, semester, department) "
            "VALUES (:name, :acronym, :level, :pid, 1, 'Integration Test Dept') "
            "RETURNING id, acronym"
        ),
        {
            "name": f"INTEGRATION_TEST_TEACHER_{suffix}",
            "acronym": f"ZZZ{suffix[:5]}",
            "level": program["level"],
            "pid": program["id"],
        },
    ).mappings().first()
    assert result is not None
    return dict(result)


def _delete_test_teacher(db: Session, teacher_id: str) -> None:
    """Delete the test teacher and all cascade-dependent rows."""
    db.execute(sa.text("DELETE FROM teachers WHERE id = :id"), {"id": teacher_id})
    db.commit()


# ---------------------------------------------------------------------------
# Scenario fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def pg_db() -> Generator[Session, None, None]:
    """Real PostgreSQL Session (autocommit=False). The test is responsible for commit/rollback."""
    engine = _make_pg_engine()
    PGSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = PGSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(scope="function")
def pg_client(pg_db: Session) -> Generator[TestClient, None, None]:
    """TestClient that uses the real PostgreSQL session."""
    from app.db import get_db
    from app.main import app

    def _override() -> Generator[Session, None, None]:
        yield pg_db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Scenario 1: POST creates a DRAFT → GET verifies contents
# ---------------------------------------------------------------------------

@_requires_pg
def test_integration_draft_create_and_get(pg_db: Session, pg_client: TestClient) -> None:
    """
    Integration Scenario 1:
    POST a timetable for a test teacher + unique academic year.
    GET the same teacher/year/day/status=DRAFT and verify the returned
    schedule matches what was written.
    """
    suffix = _unique_suffix()
    program = _get_active_program(pg_db)
    slot_codes = _get_slot_codes(pg_db)
    assert slot_codes, "No active time slots found in the database"

    teacher = _create_test_teacher(pg_db, program, suffix)
    pg_db.commit()
    teacher_id = str(teacher["id"])
    academic_year = "2099-2100"  # sentinel year, unique per test via distinct teacher_id

    try:
        # POST: create a new DRAFT timetable
        payload = {
            "academic_year": academic_year,
            "days": {
                "monday": [
                    {
                        "slot_ids": [slot_codes[0]],
                        "entry_type": "CLASS",
                        "subject_or_activity": f"Integration Test Subject {suffix}",
                        "section": "MCA-TEST",
                        "room": "T01",
                        "notes": None,
                    }
                ]
            },
        }
        post_resp = pg_client.post(f"/api/v1/teachers/{teacher_id}/timetable", json=payload)
        assert post_resp.status_code == 201, f"POST failed: {post_resp.text}"

        post_data = post_resp.json()
        assert post_data["academic_year"] == academic_year
        assert post_data["status"] == "DRAFT"
        assert "monday" in post_data["days"]
        created_entry = post_data["days"]["monday"][0]
        assert created_entry["subject_or_activity"] == f"Integration Test Subject {suffix}"
        assert slot_codes[0] in created_entry["slot_codes"]

        # GET: retrieve the same DRAFT day and verify round-trip
        get_resp = pg_client.get(
            f"/api/v1/teachers/{teacher_id}/timetable",
            params={"day": "monday", "academic_year": academic_year, "status": "DRAFT"},
        )
        assert get_resp.status_code == 200, f"GET failed: {get_resp.text}"

        get_data = get_resp.json()
        assert get_data["day"] == "monday"
        assert get_data["academic_year"] == academic_year
        assert get_data["timetable_status"] == "DRAFT"
        assert get_data["teacher_id"] == teacher_id

        # Find the period that matches slot_codes[0]
        target_period = next(
            (p for p in get_data["periods"] if p.get("code") == slot_codes[0]),
            None,
        )
        assert target_period is not None, f"Slot {slot_codes[0]} not found in GET response periods"
        assert target_period["entry"] is not None, "Expected an entry in the first slot"
        assert target_period["entry"]["subject_or_activity"] == f"Integration Test Subject {suffix}"
        assert slot_codes[0] in target_period["entry"]["slot_codes"]

    finally:
        _delete_test_teacher(pg_db, teacher_id)


# ---------------------------------------------------------------------------
# Scenario 2: PUT atomically replaces all draft entries
# ---------------------------------------------------------------------------

@_requires_pg
def test_integration_draft_put_replaces_atomically(pg_db: Session, pg_client: TestClient) -> None:
    """
    Integration Scenario 2:
    Create a DRAFT with one schedule → PUT a replacement → verify:
      - old entries are gone
      - new entries exist
      - replacement is atomic (no partial state visible after PUT)
    """
    suffix = _unique_suffix()
    program = _get_active_program(pg_db)
    slot_codes = _get_slot_codes(pg_db)
    assert len(slot_codes) >= 3, "Need at least 3 active slots for this test (S1, S2, S3)"

    teacher = _create_test_teacher(pg_db, program, suffix)
    pg_db.commit()
    teacher_id = str(teacher["id"])
    academic_year = "2099-2100"  # sentinel year, unique per test via distinct teacher_id

    try:
        # Step 1: POST initial DRAFT — CLASS in slot S1 only
        payload_v1 = {
            "academic_year": academic_year,
            "days": {
                "tuesday": [
                    {
                        "slot_ids": [slot_codes[0]],
                        "entry_type": "CLASS",
                        "subject_or_activity": f"ORIGINAL_{suffix}",
                    }
                ]
            },
        }
        post_resp = pg_client.post(f"/api/v1/teachers/{teacher_id}/timetable", json=payload_v1)
        assert post_resp.status_code == 201, f"POST failed: {post_resp.text}"

        # Step 2: PUT replacement — LAB with two consecutive slots (slot_codes[1]+slot_codes[2])
        # slot_codes[1]=S2 and slot_codes[2]=S3 are adjacent with no break between them.
        payload_v2 = {
            "academic_year": academic_year,
            "days": {
                "tuesday": [
                    {
                        "slot_ids": [slot_codes[1], slot_codes[2]],
                        "entry_type": "LAB",
                        "subject_or_activity": f"REPLACED_{suffix}",
                    }
                ]
            },
        }
        put_resp = pg_client.put(f"/api/v1/teachers/{teacher_id}/timetable", json=payload_v2)
        assert put_resp.status_code == 200, f"PUT failed: {put_resp.text}"

        # Step 3: GET and verify new LAB entry is present on S2 and S3, old S1 is empty
        get_resp = pg_client.get(
            f"/api/v1/teachers/{teacher_id}/timetable",
            params={"day": "tuesday", "academic_year": academic_year, "status": "DRAFT"},
        )
        assert get_resp.status_code == 200
        periods = get_resp.json()["periods"]

        old_slot_period = next((p for p in periods if p.get("code") == slot_codes[0]), None)
        new_slot_s2 = next((p for p in periods if p.get("code") == slot_codes[1]), None)
        new_slot_s3 = next((p for p in periods if p.get("code") == slot_codes[2]), None)

        assert old_slot_period is not None
        assert old_slot_period["entry"] is None, (
            f"Old slot {slot_codes[0]} still has an entry after PUT — replacement was not atomic"
        )
        assert new_slot_s2 is not None
        assert new_slot_s2["entry"] is not None, (
            f"LAB slot {slot_codes[1]} has no entry after PUT"
        )
        assert new_slot_s2["entry"]["subject_or_activity"] == f"REPLACED_{suffix}"
        assert new_slot_s2["entry"]["entry_type"] == "LAB"
        # A multi-slot LAB entry is attached to both periods
        assert new_slot_s3 is not None
        assert new_slot_s3["entry"] is not None, (
            f"LAB slot {slot_codes[2]} has no entry after PUT (multi-slot LAB must appear on both periods)"
        )
        assert new_slot_s3["entry"]["subject_or_activity"] == f"REPLACED_{suffix}"

        # Step 4: Confirm no reference to the original subject exists
        all_entries_with_subject = [
            p for p in periods
            if p.get("entry") and f"ORIGINAL_{suffix}" in (p["entry"].get("subject_or_activity") or "")
        ]
        assert not all_entries_with_subject, "Original entries survived the PUT replacement"

    finally:
        _delete_test_teacher(pg_db, teacher_id)


# ---------------------------------------------------------------------------
# Scenario 3: CONFIRMED → DRAFT request returns 404 (no fallback)
# ---------------------------------------------------------------------------

@_requires_pg
def test_integration_confirmed_not_returned_for_draft_request(pg_db: Session, pg_client: TestClient) -> None:
    """
    Integration Scenario 3:
    Create only a CONFIRMED timetable (via direct SQL, since there is no
    /confirm endpoint in Day-1) → GET with status=DRAFT → assert 404.
    This verifies there is no CONFIRMED→DRAFT fallback.
    """
    suffix = _unique_suffix()
    program = _get_active_program(pg_db)
    slot_codes = _get_slot_codes(pg_db)
    assert slot_codes

    teacher = _create_test_teacher(pg_db, program, suffix)
    pg_db.commit()
    teacher_id = str(teacher["id"])
    academic_year = "2099-2100"  # sentinel year, unique per test via distinct teacher_id

    try:
        # Insert a CONFIRMED timetable directly (Day-1 has no /confirm endpoint).
        # This is the only way to create a CONFIRMED record without adding a new
        # production endpoint solely for tests.
        pg_db.execute(
            sa.text(
                "INSERT INTO timetables "
                "(teacher_id, academic_year, status, source) "
                "VALUES (:tid, :year, 'CONFIRMED', 'MANUAL')"
            ),
            {"tid": teacher_id, "year": academic_year},
        )
        pg_db.commit()

        # Verify GET with status=CONFIRMED succeeds (the timetable exists)
        get_confirmed = pg_client.get(
            f"/api/v1/teachers/{teacher_id}/timetable",
            params={"day": "monday", "academic_year": academic_year, "status": "CONFIRMED"},
        )
        assert get_confirmed.status_code == 200, (
            f"Expected 200 for CONFIRMED GET but got {get_confirmed.status_code}: {get_confirmed.text}"
        )
        assert get_confirmed.json()["timetable_status"] == "CONFIRMED"

        # The critical assertion: GET with status=DRAFT must return 404.
        # The endpoint must NOT fall back to the CONFIRMED timetable.
        get_draft = pg_client.get(
            f"/api/v1/teachers/{teacher_id}/timetable",
            params={"day": "monday", "academic_year": academic_year, "status": "DRAFT"},
        )
        assert get_draft.status_code == 404, (
            f"Expected 404 for DRAFT GET (only CONFIRMED exists) but got "
            f"{get_draft.status_code}: {get_draft.text}"
        )

    finally:
        _delete_test_teacher(pg_db, teacher_id)


# ---------------------------------------------------------------------------
# Scenario 4: Deferred trigger rejects overlapping confirmed slot occupancy
# ---------------------------------------------------------------------------

@_requires_pg
def test_integration_deferred_trigger_rejects_overlapping_confirmed(pg_db: Session, pg_client: TestClient) -> None:
    """
    Integration Scenario 4 (PostgreSQL-only):
    Verify that the deferred constraint trigger (trg_timetable_validate_confirmation)
    prevents a timetable with overlapping slot occupancy from being CONFIRMED.

    Mechanism:
    1. POST a DRAFT with two entries that claim the same slot on the same day.
       (This is allowed at the DRAFT level — the trigger only fires for CONFIRMED.)
    2. Directly UPDATE the timetable status to 'CONFIRMED' in the database.
    3. Commit → the deferred trigger fires → must raise IntegrityError/OperationalError.
    4. Verify the timetable status remains DRAFT (no partial confirmation).

    Note: The Day-1 duplicate-slot Pydantic validator on TimetableWriteIn prevents
    sending overlapping entries through the API endpoint (it raises 422 before DB).
    So we insert the second conflicting entry directly via raw SQL after the POST,
    then attempt to confirm — this tests the DB trigger in isolation.
    """
    suffix = _unique_suffix()
    program = _get_active_program(pg_db)
    slot_codes = _get_slot_codes(pg_db)
    assert slot_codes, "Need at least 1 active slot"

    teacher = _create_test_teacher(pg_db, program, suffix)
    pg_db.commit()
    teacher_id = str(teacher["id"])
    academic_year = "2099-2100"  # sentinel year, unique per test via distinct teacher_id

    try:
        # Step 1: POST a clean DRAFT with one entry (no conflict yet)
        payload = {
            "academic_year": academic_year,
            "days": {
                "wednesday": [
                    {
                        "slot_ids": [slot_codes[0]],
                        "entry_type": "CLASS",
                        "subject_or_activity": f"TriggerTest_Entry1_{suffix}",
                    }
                ]
            },
        }
        post_resp = pg_client.post(f"/api/v1/teachers/{teacher_id}/timetable", json=payload)
        assert post_resp.status_code == 201, f"POST failed: {post_resp.text}"

        # Retrieve the timetable_id for direct SQL operations
        timetable_row = pg_db.execute(
            sa.text(
                "SELECT id FROM timetables "
                "WHERE teacher_id = :tid AND academic_year = :year AND status = 'DRAFT'"
            ),
            {"tid": teacher_id, "year": academic_year},
        ).fetchone()
        assert timetable_row is not None
        timetable_id = str(timetable_row[0])

        # Retrieve the time_slot_id for slot_codes[0]
        slot_row = pg_db.execute(
            sa.text("SELECT id FROM time_slots WHERE code = :code"),
            {"code": slot_codes[0]},
        ).fetchone()
        assert slot_row is not None
        time_slot_id = str(slot_row[0])

        # Step 2: Inject a second conflicting entry directly via raw SQL.
        # (The API Pydantic validator would reject this at request time, so we
        # bypass the API intentionally to create the invalid-for-confirmed state.)
        pg_db.execute(
            sa.text(
                "INSERT INTO schedule_entries (timetable_id, day_of_week, entry_type, subject_or_activity) "
                "VALUES (:tid, 3, 'CLASS', :subj) RETURNING id"
            ),
            {"tid": timetable_id, "subj": f"TriggerTest_CONFLICT_{suffix}"},
        )
        conflict_entry_row = pg_db.execute(
            sa.text(
                "SELECT id FROM schedule_entries WHERE timetable_id = :tid AND subject_or_activity = :subj"
            ),
            {"tid": timetable_id, "subj": f"TriggerTest_CONFLICT_{suffix}"},
        ).fetchone()
        assert conflict_entry_row is not None
        conflict_entry_id = str(conflict_entry_row[0])

        # Link the conflicting entry to the same time slot (same day=wednesday=3, same slot)
        pg_db.execute(
            sa.text(
                "INSERT INTO schedule_entry_slots (schedule_entry_id, time_slot_id) "
                "VALUES (:eid, :sid)"
            ),
            {"eid": conflict_entry_id, "sid": time_slot_id},
        )
        # Flush without committing — deferred triggers fire at commit, not flush.
        pg_db.flush()

        # Step 3: Attempt to confirm the timetable → trigger fires at commit → must fail.
        pg_db.execute(
            sa.text(
                "UPDATE timetables SET status = 'CONFIRMED' WHERE id = :id"
            ),
            {"id": timetable_id},
        )

        conflict_raised = False
        try:
            pg_db.commit()
        except (IntegrityError, sa.exc.DatabaseError) as exc:
            conflict_raised = True
            pg_db.rollback()
            # Verify the error mentions occupancy or overlapping
            err_str = str(exc).lower()
            assert any(
                keyword in err_str
                for keyword in ("overlapping", "occupancy", "23514", "constraint")
            ), f"Unexpected error message: {exc}"

        assert conflict_raised, (
            "Expected the deferred constraint trigger to raise an error when confirming "
            "a timetable with overlapping slot occupancy, but the commit succeeded."
        )

        # Step 4: Verify the timetable was NOT confirmed (trigger rolled back the change).
        # Open a fresh connection to see the committed state.
        verify_engine = _make_pg_engine()
        VerifySession = sessionmaker(bind=verify_engine, autocommit=False, autoflush=False)
        verify_session = VerifySession()
        try:
            row = verify_session.execute(
                sa.text("SELECT status FROM timetables WHERE id = :id"),
                {"id": timetable_id},
            ).fetchone()
            # The timetable should still be DRAFT (or gone if DELETE cascaded).
            # It must NOT be CONFIRMED.
            if row is not None:
                assert row[0] != "CONFIRMED", (
                    "Timetable was confirmed despite the trigger — DB constraint is not working!"
                )
        finally:
            verify_session.close()
            verify_engine.dispose()

    finally:
        _delete_test_teacher(pg_db, teacher_id)


# ---------------------------------------------------------------------------
# Phase 4: Department-Aware Teacher Identity Constraint Tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def apply_department_aware_migration(pg_db: Session) -> Generator[None, None, None]:
    """Apply Phase 4 migration before department-aware tests.
    
    This fixture ensures the database has the new department-aware constraint.
    It's safe to run multiple times (uses IF NOT EXISTS / IF EXISTS).
    """
    # Check if migration is needed
    result = pg_db.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_teachers_acronym_department_normalized'"
    )).first()
    
    if result is None:
        # Apply the migration
        pg_db.execute(sa.text("DROP INDEX IF EXISTS uq_teachers_acronym_normalized"))
        pg_db.execute(sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_teachers_acronym_department_normalized "
            "ON teachers (lower(btrim(acronym)), lower(btrim(department)))"
        ))
        pg_db.execute(sa.text(
            "CREATE INDEX IF NOT EXISTS idx_teachers_department_normalized "
            "ON teachers (lower(btrim(department)))"
        ))
        pg_db.commit()
    yield
    # No rollback - migration should stay applied


@pytest.mark.integration
def test_department_aware_constraint_allows_same_acronym_different_departments(
    pg_db: Session, pg_client: TestClient, apply_department_aware_migration
) -> None:
    """Database constraint allows same acronym in different departments.
    
    This test proves the uq_teachers_acronym_department_normalized constraint
    works correctly at the database level, not just application validation.
    """
    from app.models.models import Program, Teacher
    
    suffix = uuid.uuid4().hex[:8]
    program_name = f"TEST_CONSTRAINT_PROGRAM_{suffix}"
    acronym = f"TC{suffix[:4]}"  # Unique acronym for this test run
    
    try:
        # Create test program
        program = Program(name=program_name, level="UG")
        pg_db.add(program)
        pg_db.commit()
        
        # Create first teacher: TC in Computer Applications
        teacher1 = Teacher(
            name="Dr. Smith",
            acronym=acronym,
            department="Computer Applications",
            level="UG",
            program_id=program.id,
            semester=1,
        )
        pg_db.add(teacher1)
        pg_db.commit()
        
        # Create second teacher: TC in Mathematics
        # This MUST succeed if department-aware constraint is active
        teacher2 = Teacher(
            name="Dr. Singh",
            acronym=acronym,
            department="Mathematics",
            level="UG",
            program_id=program.id,
            semester=1,
        )
        pg_db.add(teacher2)
        pg_db.commit()  # Should NOT raise IntegrityError
        
        # Verify both exist
        teachers = pg_db.query(Teacher).filter(
            Teacher.acronym == acronym
        ).all()
        assert len(teachers) == 2
        assert {t.department for t in teachers} == {"Computer Applications", "Mathematics"}
        
    finally:
        # Cleanup
        pg_db.rollback()  # Clear any pending state
        pg_db.query(Teacher).filter(Teacher.acronym == acronym).delete()
        pg_db.query(Program).filter(Program.name == program_name).delete()
        pg_db.commit()


@pytest.mark.integration
def test_department_aware_constraint_rejects_same_acronym_same_department(
    pg_db: Session, pg_client: TestClient, apply_department_aware_migration
) -> None:
    """Database constraint rejects duplicate (acronym, department) tuple.
    
    This test proves the constraint enforces uniqueness within a department.
    """
    from app.models.models import Program, Teacher
    from sqlalchemy.exc import IntegrityError
    
    suffix = uuid.uuid4().hex[:8]
    program_name = f"TEST_CONSTRAINT_PROGRAM_{suffix}"
    acronym = f"TR{suffix[:4]}"  # Unique acronym for this test run
    
    try:
        # Create test program
        program = Program(name=program_name, level="UG")
        pg_db.add(program)
        pg_db.commit()
        
        # Create first teacher: TR in Computer Applications
        teacher1 = Teacher(
            name="Dr. Smith",
            acronym=acronym,
            department="Computer Applications",
            level="UG",
            program_id=program.id,
            semester=1,
        )
        pg_db.add(teacher1)
        pg_db.commit()
        
        # Attempt to create duplicate: TR in Computer Applications (different name)
        # This MUST fail with IntegrityError
        teacher2 = Teacher(
            name="Dr. Different",
            acronym=acronym,
            department="Computer Applications",  # Same department!
            level="UG",
            program_id=program.id,
            semester=1,
        )
        pg_db.add(teacher2)
        
        with pytest.raises(IntegrityError) as exc_info:
            pg_db.commit()
        
        # Verify the constraint name
        assert "uq_teachers_acronym_department_normalized" in str(exc_info.value).lower()
        
    finally:
        # Cleanup (rollback the failed transaction first)
        pg_db.rollback()
        pg_db.query(Teacher).filter(Teacher.acronym == acronym).delete()
        pg_db.query(Program).filter(Program.name == program_name).delete()
        pg_db.commit()


@pytest.mark.integration
def test_department_aware_constraint_case_insensitive(
    pg_db: Session, pg_client: TestClient, apply_department_aware_migration
) -> None:
    """Database constraint is case-insensitive for both acronym and department.
    
    This test proves lower() normalization works in the constraint.
    """
    from app.models.models import Program, Teacher
    from sqlalchemy.exc import IntegrityError
    
    suffix = uuid.uuid4().hex[:8]
    program_name = f"TEST_CONSTRAINT_PROGRAM_{suffix}"
    acronym = f"TI{suffix[:4]}"  # Unique acronym for this test run
    
    try:
        # Create test program
        program = Program(name=program_name, level="UG")
        pg_db.add(program)
        pg_db.commit()
        
        # Create first teacher: ti in computer applications (lowercase)
        teacher1 = Teacher(
            name="Dr. Lower",
            acronym=acronym.lower(),
            department="computer applications",
            level="UG",
            program_id=program.id,
            semester=1,
        )
        pg_db.add(teacher1)
        pg_db.commit()
        
        # Attempt to create: TI in Computer Applications (uppercase)
        # Should fail because constraint normalizes with lower()
        teacher2 = Teacher(
            name="Dr. Upper",
            acronym=acronym.upper(),
            department="Computer Applications",
            level="UG",
            program_id=program.id,
            semester=1,
        )
        pg_db.add(teacher2)
        
        with pytest.raises(IntegrityError) as exc_info:
            pg_db.commit()
        
        assert "uq_teachers_acronym_department_normalized" in str(exc_info.value).lower()
        
        # Rollback and verify different department allows it
        pg_db.rollback()
        teacher3 = Teacher(
            name="Dr. Different Dept",
            acronym=acronym.upper(),
            department="Mathematics",  # Different department
            level="UG",
            program_id=program.id,
            semester=1,
        )
        pg_db.add(teacher3)
        pg_db.commit()  # Should succeed
        
        # Verify both exist with different departments
        teachers = pg_db.query(Teacher).filter(
            Teacher.acronym.ilike(acronym)
        ).all()
        assert len(teachers) == 2
        depts = {t.department.lower() for t in teachers}
        assert depts == {"computer applications", "mathematics"}
        
    finally:
        # Cleanup
        pg_db.rollback()  # Clear any pending state
        pg_db.query(Teacher).filter(Teacher.acronym.ilike(acronym)).delete()
        pg_db.query(Program).filter(Program.name == program_name).delete()
        pg_db.commit()


# ---------------------------------------------------------------------------
# Phase 4: Department-Aware Teacher API Integration Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_api_create_same_acronym_different_departments(
    pg_db: Session, pg_client: TestClient, apply_department_aware_migration
) -> None:
    """POST /api/v1/teachers allows same acronym in different departments.
    
    This test verifies the Teacher API correctly handles department-aware identity
    at the HTTP layer, not just database layer.
    """
    suffix = uuid.uuid4().hex[:8]
    acronym = f"TA{suffix[:4]}".upper()  # API normalizes to uppercase
    
    # Get an active program
    program = _get_active_program(pg_db)
    
    try:
        # Clean up any existing test data first
        pg_db.execute(sa.text("DELETE FROM teachers WHERE acronym = :acr"), {"acr": acronym})
        pg_db.commit()
        
        # Create first teacher: TA in Computer Applications
        resp1 = pg_client.post(
            "/api/v1/teachers",
            json={
                "name": "Dr. API Test One",
                "acronym": acronym,
                "department": "Computer Applications",
                "level": program["level"],
                "program_id": str(program["id"]),
                "semester": 1,
            },
        )
        assert resp1.status_code == 201, resp1.text
        teacher1_id = resp1.json()["id"]
        
        # Create second teacher: same acronym in Mathematics
        # This MUST succeed via API
        resp2 = pg_client.post(
            "/api/v1/teachers",
            json={
                "name": "Dr. API Test Two",
                "acronym": acronym,
                "department": "Mathematics",
                "level": program["level"],
                "program_id": str(program["id"]),
                "semester": 1,
            },
        )
        assert resp2.status_code == 201, resp2.text
        teacher2_id = resp2.json()["id"]
        
        # Verify both exist with correct departments
        from app.models.models import Teacher
        teachers = pg_db.query(Teacher).filter(Teacher.acronym == acronym).all()
        assert len(teachers) == 2
        assert {t.department for t in teachers} == {"Computer Applications", "Mathematics"}
        
    finally:
        # Cleanup via SQL (direct delete)
        pg_db.execute(sa.text("DELETE FROM teachers WHERE acronym = :acr"), {"acr": acronym})
        pg_db.commit()


@pytest.mark.integration
def test_api_create_same_acronym_same_department_conflict(
    pg_db: Session, pg_client: TestClient, apply_department_aware_migration
) -> None:
    """POST /api/v1/teachers rejects duplicate (acronym, department) with 409.
    
    This test verifies the API returns the correct HTTP status and error message
    when attempting to create a duplicate teacher identity.
    """
    suffix = uuid.uuid4().hex[:8]
    acronym = f"TB{suffix[:4]}".upper()  # API normalizes to uppercase
    department = "Computer Applications"
    
    # Get an active program
    program = _get_active_program(pg_db)
    
    try:
        # Clean up any existing test data first
        pg_db.execute(sa.text("DELETE FROM teachers WHERE acronym = :acr"), {"acr": acronym})
        pg_db.commit()
        
        # Create first teacher
        resp1 = pg_client.post(
            "/api/v1/teachers",
            json={
                "name": "Dr. First",
                "acronym": acronym,
                "department": department,
                "level": program["level"],
                "program_id": str(program["id"]),
                "semester": 1,
            },
        )
        assert resp1.status_code == 201, resp1.text
        
        # Attempt to create duplicate (same acronym, same department)
        resp2 = pg_client.post(
            "/api/v1/teachers",
            json={
                "name": "Dr. Duplicate",
                "acronym": acronym,
                "department": department,  # Same department!
                "level": program["level"],
                "program_id": str(program["id"]),
                "semester": 1,
            },
        )
        
        # Verify proper HTTP error
        assert resp2.status_code == 409, resp2.text
        error_detail = resp2.json()["detail"]
        assert acronym in error_detail
        assert department in error_detail
        assert "already exists" in error_detail.lower()
        
    finally:
        # Cleanup
        pg_db.execute(sa.text("DELETE FROM teachers WHERE acronym = :acr"), {"acr": acronym})
        pg_db.commit()


@pytest.mark.integration
def test_api_search_with_department_filter(
    pg_db: Session, pg_client: TestClient, apply_department_aware_migration
) -> None:
    """GET /api/v1/teachers/search supports department filtering.
    
    This test verifies the search endpoint correctly filters by department
    and returns only teachers from the specified department.
    """
    suffix = uuid.uuid4().hex[:8]
    acronym = f"TS{suffix[:4]}".upper()  # API normalizes to uppercase
    
    # Get an active program
    program = _get_active_program(pg_db)
    
    try:
        # Clean up any existing test data first
        pg_db.execute(sa.text("DELETE FROM teachers WHERE acronym = :acr"), {"acr": acronym})
        pg_db.commit()
        
        # Create two teachers with same acronym in different departments
        resp1 = pg_client.post(
            "/api/v1/teachers",
            json={
                "name": "Dr. CompSci",
                "acronym": acronym,
                "department": "Computer Science",
                "level": program["level"],
                "program_id": str(program["id"]),
                "semester": 1,
            },
        )
        assert resp1.status_code == 201, resp1.text
        teacher1 = resp1.json()
        
        resp2 = pg_client.post(
            "/api/v1/teachers",
            json={
                "name": "Dr. Math",
                "acronym": acronym,
                "department": "Mathematics",
                "level": program["level"],
                "program_id": str(program["id"]),
                "semester": 1,
            },
        )
        assert resp2.status_code == 201, resp2.text
        teacher2 = resp2.json()
        
        # Search without department filter - should find both
        resp_all = pg_client.get(f"/api/v1/teachers/search?q={acronym}")
        assert resp_all.status_code == 200, resp_all.text
        all_teachers = resp_all.json()
        # Filter to our test teachers only
        all_test_teachers = [t for t in all_teachers if t["acronym"] == acronym]
        assert len(all_test_teachers) == 2
        
        # Search with Computer Science department filter - should find only Dr. CompSci
        resp_cs = pg_client.get(
            f"/api/v1/teachers/search?q={acronym}&department=Computer Science"
        )
        assert resp_cs.status_code == 200, resp_cs.text
        cs_teachers = resp_cs.json()
        # Filter to our test teachers only
        cs_test_teachers = [t for t in cs_teachers if t["acronym"] == acronym]
        assert len(cs_test_teachers) == 1
        assert cs_test_teachers[0]["department"] == "Computer Science"
        assert cs_test_teachers[0]["name"] == "Dr. CompSci"
        
        # Search with Mathematics department filter - should find only Dr. Math
        resp_math = pg_client.get(
            f"/api/v1/teachers/search?q={acronym}&department=Mathematics"
        )
        assert resp_math.status_code == 200, resp_math.text
        math_teachers = resp_math.json()
        # Filter to our test teachers only
        math_test_teachers = [t for t in math_teachers if t["acronym"] == acronym]
        assert len(math_test_teachers) == 1
        assert math_test_teachers[0]["department"] == "Mathematics"
        assert math_test_teachers[0]["name"] == "Dr. Math"
        
        # Search with non-existent department - should find none of our test teachers
        resp_none = pg_client.get(
            f"/api/v1/teachers/search?q={acronym}&department=Nonexistent Department"
        )
        assert resp_none.status_code == 200, resp_none.text
        none_teachers = resp_none.json()
        none_test_teachers = [t for t in none_teachers if t["acronym"] == acronym]
        assert len(none_test_teachers) == 0
        
    finally:
        # Cleanup
        pg_db.execute(sa.text("DELETE FROM teachers WHERE acronym = :acr"), {"acr": acronym})
        pg_db.commit()
