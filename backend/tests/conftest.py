"""pytest fixtures for backend unit tests.

These tests use SQLite (in-memory) for fast, dependency-free execution.

IMPORTANT LIMITATIONS with SQLite:
1. The ORM models use PostgreSQL-specific column types (PGUUID, CHECK constraints
   with btrim, server_default with gen_random_uuid). We work around this by
   creating a SQLite-compatible schema directly via DDL and monkeypatching the
   app's database engine/session for tests.
2. SQLite does not support PostgreSQL deferred constraint triggers.
   The occupancy-validation trigger (database/schema.sql) will NOT fire here.
   To verify trigger behaviour, run against a real PostgreSQL/Supabase instance.

What these unit tests DO cover:
- Endpoint routing and HTTP status codes
- Pydantic validation (slot codes, duplicate detection, required params)
- 404 semantics for missing timetable / unknown teacher
- Academic-year and DRAFT/CONFIRMED scoping
- Round-trip slot code handling
- Multi-slot entry structure
- Transactional draft replacement (cascade delete + re-insert)
"""
from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, event as sa_event
from sqlalchemy.orm import Session, sessionmaker

import app.db as app_db_module
from app.db import get_db
from app.main import app


# ---------------------------------------------------------------------------
# SQLite-compatible DDL (strips PG-specific btrim / gen_random_uuid)
# UUID columns stored as TEXT in SQLite.
# ---------------------------------------------------------------------------

_SQLITE_DDL_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS programs (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        level TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS teachers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        acronym TEXT NOT NULL,
        level TEXT NOT NULL,
        program_id TEXT NOT NULL,
        semester INTEGER NOT NULL,
        department TEXT NOT NULL DEFAULT 'Prototype Department',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS time_slots (
        id TEXT PRIMARY KEY,
        code TEXT NOT NULL UNIQUE,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        sequence INTEGER NOT NULL UNIQUE,
        is_active INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS timetables (
        id TEXT PRIMARY KEY,
        teacher_id TEXT NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
        academic_year TEXT NOT NULL,
        effective_from TEXT,
        effective_to TEXT,
        status TEXT NOT NULL,
        source TEXT NOT NULL,
        last_verified_at TEXT,
        created_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS schedule_entries (
        id TEXT PRIMARY KEY,
        timetable_id TEXT NOT NULL REFERENCES timetables(id) ON DELETE CASCADE,
        day_of_week INTEGER NOT NULL,
        entry_type TEXT NOT NULL,
        subject_or_activity TEXT NOT NULL,
        section TEXT,
        room TEXT,
        notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS schedule_entry_slots (
        schedule_entry_id TEXT NOT NULL REFERENCES schedule_entries(id) ON DELETE CASCADE,
        time_slot_id TEXT NOT NULL REFERENCES time_slots(id),
        PRIMARY KEY (schedule_entry_id, time_slot_id)
    )""",
]


# ---------------------------------------------------------------------------
# Single shared in-memory SQLite engine for the whole test session.
# "?check_same_thread=False&uri=true" allows sharing across threads.
# Using a static file name ":memory:" with check_same_thread=False keeps a
# single in-memory database alive for the whole session.
# ---------------------------------------------------------------------------

_sqlite_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    echo=False,
)


# Enable FK enforcement on every new connection.
@sa_event.listens_for(_sqlite_engine, "connect")
def _set_sqlite_pragma(dbapi_con, _connection_record):  # type: ignore[no-untyped-def]
    cursor = dbapi_con.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_TestingSessionLocal = sessionmaker(
    bind=_sqlite_engine, autoflush=False, autocommit=False, expire_on_commit=False
)


@pytest.fixture(scope="session", autouse=True)
def _create_tables() -> None:
    """Create SQLite tables once for the whole test session."""
    with _sqlite_engine.begin() as conn:
        for ddl in _SQLITE_DDL_STATEMENTS:
            conn.execute(text(ddl))


@pytest.fixture(autouse=True)
def _patch_db_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point app.db's engine and SessionLocal at the SQLite test engine."""
    monkeypatch.setattr(app_db_module, "engine", _sqlite_engine)
    monkeypatch.setattr(app_db_module, "SessionLocal", _TestingSessionLocal)


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    """Per-test session wrapped in a savepoint; rolled back after each test."""
    connection = _sqlite_engine.connect()
    connection.begin()  # outer transaction for rollback
    session = _TestingSessionLocal(bind=connection)
    try:
        yield session
    finally:
        session.close()
        connection.rollback()
        connection.close()


@pytest.fixture()
def client(db: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers — raw SQL inserts bypass PG server_default incompatibilities
# ---------------------------------------------------------------------------


@pytest.fixture()
def program(db: Session) -> dict:
    prog_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO programs (id, name, level, is_active) VALUES (:id, :name, :level, 1)"),
        {"id": prog_id, "name": "Master of Computer Applications", "level": "PG"},
    )
    db.flush()
    return {"id": prog_id, "name": "Master of Computer Applications", "level": "PG"}


@pytest.fixture()
def teacher(db: Session, program: dict) -> dict:
    teacher_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO teachers "
            "(id, name, acronym, level, program_id, semester, department, is_active, created_at, updated_at) "
            "VALUES (:id, :name, :acronym, :level, :pid, :sem, :dept, 1, '', '')"
        ),
        {
            "id": teacher_id,
            "name": "Dr. Test Teacher",
            "acronym": "TT",
            "level": "PG",
            "pid": program["id"],
            "sem": 1,
            "dept": "Test Dept",
        },
    )
    db.flush()
    return {"id": teacher_id}


@pytest.fixture()
def time_slots(db: Session) -> list[dict]:
    """Insert the canonical S1-S9 time slots."""
    slots_data = [
        ("S1", "08:00:00", "08:55:00", 1),
        ("S2", "08:55:00", "09:50:00", 2),
        ("S3", "09:50:00", "10:45:00", 3),
        ("S4", "11:15:00", "12:10:00", 4),
        ("S5", "12:10:00", "13:05:00", 5),
        ("S6", "14:00:00", "14:55:00", 6),
        ("S7", "14:55:00", "15:50:00", 7),
        ("S8", "15:50:00", "16:45:00", 8),
        ("S9", "16:45:00", "17:40:00", 9),
    ]
    slots = []
    for code, start, end, seq in slots_data:
        slot_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO time_slots (id, code, start_time, end_time, sequence, is_active) "
                "VALUES (:id, :code, :start, :end, :seq, 1)"
            ),
            {"id": slot_id, "code": code, "start": start, "end": end, "seq": seq},
        )
        slots.append({"id": slot_id, "code": code})
    db.flush()
    return slots
