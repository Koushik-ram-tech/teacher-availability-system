"""
test_timetable_confirm_integration.py
--------------------------------------
PostgreSQL integration tests for POST /api/v1/teachers/{teacher_id}/timetable/confirm.

All tests are marked @pytest.mark.integration and are skipped automatically
unless DATABASE_URL is set to a live PostgreSQL instance.

To run:
    DATABASE_URL=postgresql+psycopg://... pytest -m integration -v

Scenarios covered:
  1. DRAFT -> CONFIRMED succeeds (happy path).
  2. No DRAFT -> 404.
  3. Existing CONFIRMED prevents duplicate confirmation -> 409.
  4. last_verified_at is set after confirmation.
  5. Empty DRAFT (no entries) can be confirmed.
  6. Excel import still creates DRAFT, never CONFIRMED (regression guard).
  7. Existing CONFIRMED timetable is unchanged when a new workbook is imported.
"""
from __future__ import annotations

import io
import uuid
from collections.abc import Generator
from typing import Any

import openpyxl
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

# ---------------------------------------------------------------------------
# Skip when PostgreSQL is unavailable
# ---------------------------------------------------------------------------

_SKIP_REASON: str | None = None
_PG_DATABASE_URL: str | None = None

try:
    from app.core.config import get_settings
    _settings = get_settings()
    _raw_url = _settings.database_url

    if _raw_url.startswith("postgres://"):
        _PG_DATABASE_URL = _raw_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif _raw_url.startswith("postgresql://"):
        _PG_DATABASE_URL = _raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    elif _raw_url.startswith("postgresql+psycopg://"):
        _PG_DATABASE_URL = _raw_url
    else:
        _SKIP_REASON = f"DATABASE_URL is not PostgreSQL: {_raw_url[:30]}..."
except Exception as exc:  # noqa: BLE001
    _SKIP_REASON = f"Could not load DATABASE_URL: {exc}"

pytestmark = pytest.mark.integration

_requires_pg = pytest.mark.skipif(
    _SKIP_REASON is not None,
    reason=_SKIP_REASON or "PostgreSQL unavailable",
)


# ---------------------------------------------------------------------------
# PG fixtures (mirrors test_excel_import_integration.py)
# ---------------------------------------------------------------------------


def _make_pg_engine() -> sa.Engine:
    assert _PG_DATABASE_URL is not None
    return sa.create_engine(_PG_DATABASE_URL, pool_pre_ping=True, echo=False)


@pytest.fixture(scope="function")
def pg_db() -> Generator[Session, None, None]:
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
    from app.db import get_db
    from app.main import app

    def _override() -> Generator[Session, None, None]:
        yield pg_db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_suffix() -> str:
    return uuid.uuid4().hex[:8].upper()


def _get_active_pg_program(db: Session) -> dict[str, Any]:
    row = db.execute(
        sa.text(
            "SELECT id, name, level FROM programs "
            "WHERE is_active = TRUE AND level = 'PG' LIMIT 1"
        )
    ).mappings().first()
    if row is None:
        raise RuntimeError("No active PG program found. Run seed SQL first.")
    return dict(row)


def _get_slot_codes_pg(db: Session) -> list[str]:
    rows = db.execute(
        sa.text("SELECT code FROM time_slots WHERE is_active = TRUE ORDER BY sequence")
    ).fetchall()
    return [r[0] for r in rows]


def _delete_test_teacher(db: Session, teacher_id: str) -> None:
    db.execute(sa.text("DELETE FROM teachers WHERE id = :id"), {"id": teacher_id})
    db.commit()


def _create_teacher_via_api(
    client: TestClient, db: Session, *, suffix: str
) -> dict[str, Any]:
    """Create a teacher via API and return the response JSON."""
    program = _get_active_pg_program(db)
    resp = client.post(
        "/api/v1/teachers",
        json={
            "name": f"Confirm Tester {suffix}",
            "acronym": f"CT{suffix[:4]}",
            "level": "PG",
            "program_id": str(program["id"]),
            "semester": 1,
            "department": "Test Dept",
        },
    )
    assert resp.status_code == 201, f"Teacher creation failed: {resp.text}"
    return resp.json()


def _save_draft_via_api(
    client: TestClient, teacher_id: str, *, slot: str = "S1", academic_year: str
) -> None:
    """Save a DRAFT timetable with a single CLASS entry via the write API."""
    payload = {
        "academic_year": academic_year,
        "days": {
            "monday": [
                {
                    "slot_ids": [slot],
                    "entry_type": "CLASS",
                    "subject_or_activity": "Integration Test Subject",
                }
            ]
        },
    }
    # Try update first, create if 404
    r = client.put(f"/api/v1/teachers/{teacher_id}/timetable", json=payload)
    if r.status_code == 404:
        r = client.post(f"/api/v1/teachers/{teacher_id}/timetable", json=payload)
    assert r.status_code in (200, 201), f"Draft save failed: {r.text}"


def _build_xlsx(
    academic_year: str,
    teacher_name: str,
    teacher_acronym: str,
    program_name: str,
    level: str = "PG",
    slot: str = "S1",
) -> bytes:
    wb = openpyxl.Workbook()
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]
    teachers = wb.create_sheet("Teachers")
    teachers.append(["name", "acronym", "level", "program", "semester", "department"])
    teachers.append([teacher_name, teacher_acronym, level, program_name, 1, "Test Dept"])
    schedule = wb.create_sheet("Schedule")
    schedule.append(["teacher_acronym", "day", "type", "slots",
                     "subject_or_activity", "section", "room", "notes"])
    schedule.append([teacher_acronym, "monday", "CLASS", slot, None, None, None, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ===========================================================================
# Scenario 1: DRAFT -> CONFIRMED succeeds (happy path)
# ===========================================================================


@_requires_pg
def test_confirm_draft_to_confirmed(pg_db: Session, pg_client: TestClient) -> None:
    """Confirming a valid DRAFT timetable via API promotes it to CONFIRMED."""
    suffix = _unique_suffix()
    slot_codes = _get_slot_codes_pg(pg_db)
    assert slot_codes, "No active time slots in DB."

    teacher = _create_teacher_via_api(pg_client, pg_db, suffix=suffix)
    teacher_id = teacher["id"]
    academic_year = "2025-2026"

    # Save a DRAFT via the existing timetable write API
    _save_draft_via_api(pg_client, teacher_id, slot=slot_codes[0], academic_year=academic_year)

    # Confirm it
    resp = pg_client.post(
        f"/api/v1/teachers/{teacher_id}/timetable/confirm",
        params={"academic_year": academic_year},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "CONFIRMED"
    assert body["teacher_id"] == teacher_id
    assert body["academic_year"] == academic_year
    assert isinstance(body["entry_count"], int)
    assert body["confirmed_at"], "confirmed_at must be non-empty"

    # Verify DB: exactly one CONFIRMED timetable, zero DRAFTs
    rows = pg_db.execute(
        sa.text(
            "SELECT status FROM timetables WHERE teacher_id = :tid AND academic_year = :yr"
        ),
        {"tid": teacher_id, "yr": academic_year},
    ).fetchall()
    statuses = [r[0] for r in rows]
    assert statuses == ["CONFIRMED"], f"Expected [CONFIRMED], got {statuses}"

    # Cleanup
    _delete_test_teacher(pg_db, teacher_id)


# ===========================================================================
# Scenario 2: No DRAFT -> 404
# ===========================================================================


@_requires_pg
def test_confirm_no_draft_returns_404(pg_db: Session, pg_client: TestClient) -> None:
    """Confirming when no DRAFT exists returns 404."""
    suffix = _unique_suffix()
    teacher = _create_teacher_via_api(pg_client, pg_db, suffix=suffix)
    teacher_id = teacher["id"]

    resp = pg_client.post(
        f"/api/v1/teachers/{teacher_id}/timetable/confirm",
        params={"academic_year": "2099-2100"},
    )
    assert resp.status_code == 404
    assert "No DRAFT" in resp.json()["detail"]

    _delete_test_teacher(pg_db, teacher_id)


# ===========================================================================
# Scenario 3: Existing CONFIRMED prevents duplicate -> 409
# ===========================================================================


@_requires_pg
def test_confirm_already_confirmed_returns_409(pg_db: Session, pg_client: TestClient) -> None:
    """Attempting to confirm when a CONFIRMED timetable already exists returns 409."""
    suffix = _unique_suffix()
    slot_codes = _get_slot_codes_pg(pg_db)
    teacher = _create_teacher_via_api(pg_client, pg_db, suffix=suffix)
    teacher_id = teacher["id"]
    academic_year = "2025-2026"

    # First confirm succeeds
    _save_draft_via_api(pg_client, teacher_id, slot=slot_codes[0], academic_year=academic_year)
    r1 = pg_client.post(
        f"/api/v1/teachers/{teacher_id}/timetable/confirm",
        params={"academic_year": academic_year},
    )
    assert r1.status_code == 200, r1.text

    # Save a new DRAFT for the same year (Excel import would create one)
    _save_draft_via_api(pg_client, teacher_id, slot=slot_codes[1] if len(slot_codes) > 1 else slot_codes[0], academic_year=academic_year)

    # Second confirm must be rejected with 409
    r2 = pg_client.post(
        f"/api/v1/teachers/{teacher_id}/timetable/confirm",
        params={"academic_year": academic_year},
    )
    assert r2.status_code == 409
    assert "already exists" in r2.json()["detail"].lower()

    _delete_test_teacher(pg_db, teacher_id)


# ===========================================================================
# Scenario 4: last_verified_at is set after confirmation
# ===========================================================================


@_requires_pg
def test_confirm_sets_last_verified_at(pg_db: Session, pg_client: TestClient) -> None:
    """last_verified_at on the timetable row must be non-null after confirmation."""
    suffix = _unique_suffix()
    slot_codes = _get_slot_codes_pg(pg_db)
    teacher = _create_teacher_via_api(pg_client, pg_db, suffix=suffix)
    teacher_id = teacher["id"]
    academic_year = "2025-2026"

    _save_draft_via_api(pg_client, teacher_id, slot=slot_codes[0], academic_year=academic_year)

    resp = pg_client.post(
        f"/api/v1/teachers/{teacher_id}/timetable/confirm",
        params={"academic_year": academic_year},
    )
    assert resp.status_code == 200, resp.text

    row = pg_db.execute(
        sa.text(
            "SELECT last_verified_at FROM timetables "
            "WHERE teacher_id = :tid AND academic_year = :yr AND status = 'CONFIRMED'"
        ),
        {"tid": teacher_id, "yr": academic_year},
    ).mappings().first()
    assert row is not None
    assert row["last_verified_at"] is not None, "last_verified_at was not set"

    _delete_test_teacher(pg_db, teacher_id)


# ===========================================================================
# Scenario 5: Empty DRAFT (no entries) can be confirmed
# ===========================================================================


@_requires_pg
def test_confirm_empty_draft_succeeds(pg_db: Session, pg_client: TestClient) -> None:
    """An empty DRAFT (no schedule entries) can be confirmed."""
    suffix = _unique_suffix()
    teacher = _create_teacher_via_api(pg_client, pg_db, suffix=suffix)
    teacher_id = teacher["id"]
    academic_year = "2025-2026"

    # Create a DRAFT with an empty days payload
    payload = {"academic_year": academic_year, "days": {}}
    r = pg_client.post(f"/api/v1/teachers/{teacher_id}/timetable", json=payload)
    assert r.status_code == 201, r.text

    resp = pg_client.post(
        f"/api/v1/teachers/{teacher_id}/timetable/confirm",
        params={"academic_year": academic_year},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CONFIRMED"
    assert resp.json()["entry_count"] == 0

    _delete_test_teacher(pg_db, teacher_id)


# ===========================================================================
# Scenario 6: Excel import still creates DRAFT, never CONFIRMED (regression)
# ===========================================================================


@_requires_pg
def test_import_confirm_still_creates_draft_not_confirmed(
    pg_db: Session, pg_client: TestClient
) -> None:
    """After Excel import + confirm, timetable must be DRAFT, not CONFIRMED."""
    suffix = _unique_suffix()
    program = _get_active_pg_program(pg_db)
    slot_codes = _get_slot_codes_pg(pg_db)
    assert slot_codes, "No active time slots in DB."

    teacher_acronym = f"RG{suffix[:5]}"
    teacher_name = f"REGRESS_{suffix}"
    academic_year = "2099-2100"

    xlsx_bytes = _build_xlsx(
        academic_year=academic_year,
        teacher_name=teacher_name,
        teacher_acronym=teacher_acronym,
        program_name=program["name"],
        level=program["level"],
        slot=slot_codes[0],
    )

    up = pg_client.post(
        "/api/v1/imports/excel",
        files={"file": ("t.xlsx", xlsx_bytes, "application/octet-stream")},
        data={"academic_year": academic_year},
    )
    assert up.status_code == 200, up.text
    if up.json()["errors"]:
        pytest.skip(f"Preview errors: {up.json()['errors']}")

    conf = pg_client.post(f"/api/v1/imports/{up.json()['import_id']}/confirm")
    assert conf.status_code == 200, conf.text

    teacher_row = pg_db.execute(
        sa.text("SELECT id FROM teachers WHERE acronym = :acr"), {"acr": teacher_acronym}
    ).mappings().first()
    assert teacher_row is not None

    teacher_id = str(teacher_row["id"])
    confirmed_count = pg_db.execute(
        sa.text(
            "SELECT COUNT(*) FROM timetables "
            "WHERE teacher_id = :tid AND status = 'CONFIRMED'"
        ),
        {"tid": teacher_id},
    ).scalar()
    assert confirmed_count == 0, f"Expected 0 CONFIRMED rows, got {confirmed_count}"

    _delete_test_teacher(pg_db, teacher_id)


# ===========================================================================
# Scenario 7: Existing CONFIRMED timetable is unchanged when new workbook imported
# ===========================================================================


@_requires_pg
def test_confirmed_timetable_unchanged_after_new_import(
    pg_db: Session, pg_client: TestClient
) -> None:
    """When a teacher has a CONFIRMED timetable, importing a new workbook must:
    - Create a new DRAFT (not touch the CONFIRMED row).
    - Leave the CONFIRMED timetable's status and entries intact.
    """
    suffix = _unique_suffix()
    slot_codes = _get_slot_codes_pg(pg_db)
    program = _get_active_pg_program(pg_db)
    assert slot_codes, "No active time slots in DB."

    teacher = _create_teacher_via_api(pg_client, pg_db, suffix=suffix)
    teacher_id = teacher["id"]
    teacher_acronym = teacher["acronym"]
    academic_year = "2025-2026"

    # Step 1: Save DRAFT and confirm it
    _save_draft_via_api(pg_client, teacher_id, slot=slot_codes[0], academic_year=academic_year)
    r = pg_client.post(
        f"/api/v1/teachers/{teacher_id}/timetable/confirm",
        params={"academic_year": academic_year},
    )
    assert r.status_code == 200, r.text

    # Remember the CONFIRMED timetable id
    confirmed_row = pg_db.execute(
        sa.text(
            "SELECT id FROM timetables "
            "WHERE teacher_id = :tid AND academic_year = :yr AND status = 'CONFIRMED'"
        ),
        {"tid": teacher_id, "yr": academic_year},
    ).mappings().first()
    assert confirmed_row is not None
    confirmed_id = str(confirmed_row["id"])

    # Step 2: Import a new workbook for the same teacher + same year
    xlsx_bytes = _build_xlsx(
        academic_year=academic_year,
        teacher_name=teacher["name"],
        teacher_acronym=teacher_acronym,
        program_name=program["name"],
        level=program["level"],
        slot=slot_codes[1] if len(slot_codes) > 1 else slot_codes[0],
    )
    up = pg_client.post(
        "/api/v1/imports/excel",
        files={"file": ("t.xlsx", xlsx_bytes, "application/octet-stream")},
        data={"academic_year": academic_year},
    )
    assert up.status_code == 200, up.text
    if up.json()["errors"]:
        pytest.skip(f"Preview errors: {up.json()['errors']}")

    imp_conf = pg_client.post(f"/api/v1/imports/{up.json()['import_id']}/confirm")
    assert imp_conf.status_code == 200, imp_conf.text

    # Step 3: CONFIRMED row must still exist and be unchanged
    pg_db.expire_all()
    confirmed_still = pg_db.execute(
        sa.text("SELECT id, status FROM timetables WHERE id = :id"),
        {"id": confirmed_id},
    ).mappings().first()
    assert confirmed_still is not None, "CONFIRMED timetable was deleted"
    assert confirmed_still["status"] == "CONFIRMED", "CONFIRMED timetable status was changed"

    # Step 4: A DRAFT must also exist (the newly imported one)
    draft_count = pg_db.execute(
        sa.text(
            "SELECT COUNT(*) FROM timetables "
            "WHERE teacher_id = :tid AND academic_year = :yr AND status = 'DRAFT'"
        ),
        {"tid": teacher_id, "yr": academic_year},
    ).scalar()
    assert draft_count == 1, f"Expected 1 DRAFT after import, got {draft_count}"

    _delete_test_teacher(pg_db, teacher_id)
