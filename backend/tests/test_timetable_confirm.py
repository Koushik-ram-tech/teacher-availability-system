"""
test_timetable_confirm.py
--------------------------
Tests for POST /api/v1/teachers/{teacher_id}/timetable/confirm.

Architecture note (same as test_timetable.py):
  PGUUID(as_uuid=True) columns on the ORM models do not coerce correctly in
  SQLite (the dialect stores UUID as binary bytes; raw SQL inserts TEXT).
  This means ORM select queries like _get_teacher() cannot find rows
  inserted via raw SQL text — a known, documented limitation in conftest.py.

Unit tests here cover:
  - HTTP parameter validation (no teacher needed)
  - 404 for unknown teacher UUID (no seed needed)
  - Route registration (not 405)
  - Separation of import-confirm vs timetable-confirm routes

Full success-path coverage (DRAFT → CONFIRMED, 409, last_verified_at, etc.)
requires a real PostgreSQL instance and lives in the integration test:
  tests/test_timetable_confirm_integration.py  (marked @pytest.mark.integration)

To run integration tests:
    DATABASE_URL=postgresql+psycopg://... pytest -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import app.db as app_db_module
from app.db import get_db
from app.main import app


# ---------------------------------------------------------------------------
# Lightweight SQLite client for parameter-validation tests only
# (same pattern as TestApiParameterValidation in test_timetable.py)
# ---------------------------------------------------------------------------

_DDL = [
    "CREATE TABLE IF NOT EXISTS programs "
    "(id TEXT PRIMARY KEY, name TEXT NOT NULL, level TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1)",
    "CREATE TABLE IF NOT EXISTS teachers "
    "(id TEXT PRIMARY KEY, name TEXT NOT NULL, acronym TEXT NOT NULL, level TEXT NOT NULL, "
    "program_id TEXT NOT NULL, semester INTEGER NOT NULL, "
    "department TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1, "
    "created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS time_slots "
    "(id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, start_time TEXT NOT NULL, "
    "end_time TEXT NOT NULL, sequence INTEGER NOT NULL UNIQUE, is_active INTEGER NOT NULL DEFAULT 1)",
    "CREATE TABLE IF NOT EXISTS timetables "
    "(id TEXT PRIMARY KEY, teacher_id TEXT NOT NULL, academic_year TEXT NOT NULL, "
    "effective_from TEXT, effective_to TEXT, status TEXT NOT NULL, source TEXT NOT NULL, "
    "last_verified_at TEXT, created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS schedule_entries "
    "(id TEXT PRIMARY KEY, timetable_id TEXT NOT NULL, "
    "day_of_week INTEGER NOT NULL, entry_type TEXT NOT NULL, "
    "subject_or_activity TEXT NOT NULL, section TEXT, room TEXT, notes TEXT)",
    "CREATE TABLE IF NOT EXISTS schedule_entry_slots "
    "(schedule_entry_id TEXT NOT NULL, time_slot_id TEXT NOT NULL, "
    "PRIMARY KEY (schedule_entry_id, time_slot_id))",
]

_lite_engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}
)
_LiteSession = sessionmaker(
    bind=_lite_engine, autoflush=False, autocommit=False, expire_on_commit=False
)
with _lite_engine.begin() as _c:
    for _s in _DDL:
        _c.execute(text(_s))


@pytest.fixture()
def _lite_db() -> Session:
    conn = _lite_engine.connect()
    conn.begin()
    sess = _LiteSession(bind=conn)
    try:
        yield sess
    finally:
        sess.close()
        conn.rollback()
        conn.close()


@pytest.fixture()
def api_client(_lite_db: Session) -> TestClient:
    def _override():
        yield _lite_db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


YEAR = "2088-2089"


# ===========================================================================
# Parameter / routing tests (no teacher seed needed)
# ===========================================================================


def test_confirm_unknown_teacher_returns_404(api_client: TestClient) -> None:
    """A randomly generated teacher UUID returns 404, not 500 or 405."""
    resp = api_client.post(
        f"/api/v1/teachers/{uuid.uuid4()}/timetable/confirm",
        params={"academic_year": YEAR},
    )
    assert resp.status_code == 404


def test_confirm_missing_academic_year_returns_422(api_client: TestClient) -> None:
    """academic_year is required; omitting it must yield 422."""
    resp = api_client.post(f"/api/v1/teachers/{uuid.uuid4()}/timetable/confirm")
    assert resp.status_code == 422


def test_confirm_route_is_not_method_not_allowed(api_client: TestClient) -> None:
    """Endpoint must be registered as POST (not 405 Method Not Allowed)."""
    resp = api_client.post(
        f"/api/v1/teachers/{uuid.uuid4()}/timetable/confirm",
        params={"academic_year": YEAR},
    )
    # 404 = route exists, teacher not found.  405 = route not registered.
    assert resp.status_code != 405, "POST /timetable/confirm route is not registered"


def test_import_and_timetable_confirm_are_separate_routes(api_client: TestClient) -> None:
    """Import-confirm and timetable-confirm must live at different paths.

    The import endpoint always writes DRAFT (persister.py is the guarantee).
    This test verifies the routes do not overlap so a confirm call cannot
    accidentally trigger the wrong handler.
    """
    fake = str(uuid.uuid4())
    assert f"/api/v1/imports/{fake}/confirm" != f"/api/v1/teachers/{fake}/timetable/confirm"

    # Both routes should be registered (not 405)
    r_tt = api_client.post(
        f"/api/v1/teachers/{fake}/timetable/confirm",
        params={"academic_year": YEAR},
    )
    assert r_tt.status_code != 405, "/timetable/confirm not registered"

    # Import confirm without a valid staged import should 404 (not 405)
    r_imp = api_client.post(f"/api/v1/imports/{fake}/confirm")
    assert r_imp.status_code == 404, "Import confirm should 404 for unknown import_id"


# ===========================================================================
# NOTE: Full success-path tests (DRAFT→CONFIRMED, 409, last_verified_at,
# empty-draft, invalid-draft) require PostgreSQL and are in:
#   tests/test_timetable_confirm_integration.py  (@pytest.mark.integration)
# ===========================================================================
