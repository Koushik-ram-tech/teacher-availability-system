"""
tests/test_multi_resource.py
Regression tests for multi-resource per activity group support.

Covers:
  - Multiple resources in one group → separate resource rows (no composite name)
  - schedule_entry_resources join table populated correctly
  - resource_id (singular FK) set to first resource for backwards compat
  - Single-resource case unchanged (backwards compat)
  - Legacy room-only fallback (resource_codes=[]) still works
  - Three resources in one group
"""
import uuid
from datetime import time

import pytest
from sqlalchemy import func, select

from app.models.models import (
    Program,
    Resource,
    ScheduleEntry,
    ScheduleEntryResource,
    TimeSlot,
    Timetable,
)
from app.schemas.imports import ImportPreview, ScheduleImportRow, TeacherImportRow
from app.services.excel_import.persister import persist_import


# ---------------------------------------------------------------------------
# Fixture helpers shared by all tests in this module
# ---------------------------------------------------------------------------

SLOT_CODES = [
    ("S1", time(8, 0), time(8, 55), 1),
    ("S2", time(8, 55), time(9, 50), 2),
    ("S4", time(11, 15), time(12, 10), 4),
    ("S5", time(12, 10), time(13, 5), 5),
    ("S7", time(14, 55), time(15, 50), 7),
    ("S8", time(15, 50), time(16, 45), 8),
]

ACADEMIC_YEAR = "2099-2100"


@pytest.fixture()
def seeded_db(db):
    """DB session with time slots + MCA PG program pre-seeded."""
    # Seed time slots
    for code, start, end, seq in SLOT_CODES:
        db.add(TimeSlot(
            id=uuid.uuid4(), code=code,
            start_time=start, end_time=end,
            sequence=seq, is_active=True,
        ))
    # Seed program matching what TeacherImportRow uses
    db.add(Program(
        id=uuid.uuid4(),
        name="MCA",
        level="PG",
        is_active=True,
    ))
    db.commit()
    return db


def _preview(teacher_acronym: str, day: str, slot_ids: list[str],
             room: str | None, resource_codes: list[str]) -> ImportPreview:
    row = ScheduleImportRow(
        row_refs=["test!1"],
        teacher_acronym=teacher_acronym,
        day=day,
        slot_ids=slot_ids,
        entry_type="CLASS",
        subject_or_activity="TestSubject",
        section="I-A",
        room=room,
        resource_codes=resource_codes,
    )
    teacher = TeacherImportRow(
        row_ref="Teachers!2",
        action="CREATE",
        name="Test Teacher",
        acronym=teacher_acronym,
        level="PG",
        program_name="MCA",
        semester=1,
        department="Computer Applications",
    )
    return ImportPreview(
        import_id="test-import-id",
        academic_year=ACADEMIC_YEAR,
        teachers=[teacher],
        days={day: [row]},
    )


def _entry(seeded_db, acronym: str) -> ScheduleEntry:
    tt = seeded_db.scalar(
        select(Timetable)
        .where(Timetable.academic_year == ACADEMIC_YEAR)
    )
    assert tt is not None, "Timetable not created"
    entry = seeded_db.scalar(
        select(ScheduleEntry).where(ScheduleEntry.timetable_id == tt.id)
    )
    assert entry is not None, "ScheduleEntry not created"
    return entry


# ---------------------------------------------------------------------------
# Test 1: Two resources → two separate Resource rows, no composite
# ---------------------------------------------------------------------------

def test_multi_resource_creates_separate_rows(seeded_db):
    """CA3 and FDC must be two separate rows; 'CA3, FDC' must never exist."""
    preview = _preview("SU", "wednesday", ["S4", "S5"],
                       room="CA3, FDC", resource_codes=["CA3", "FDC"])
    persist_import(preview, seeded_db)
    seeded_db.commit()

    composite = seeded_db.scalar(
        select(func.count(Resource.id)).where(Resource.name == "CA3, FDC")
    )
    assert composite == 0, "Composite resource 'CA3, FDC' must not exist"

    ca3 = seeded_db.scalar(select(Resource).where(Resource.name == "CA3"))
    assert ca3 is not None, "CA3 resource row missing"

    fdc = seeded_db.scalar(select(Resource).where(Resource.name == "FDC"))
    assert fdc is not None, "FDC resource row missing"


# ---------------------------------------------------------------------------
# Test 2: Join table populated with one row per resource
# ---------------------------------------------------------------------------

def test_multi_resource_join_table_populated(seeded_db):
    """schedule_entry_resources must have 2 rows for a 2-resource group."""
    preview = _preview("DNS", "wednesday", ["S4", "S5"],
                       room="CA3, FDC", resource_codes=["CA3", "FDC"])
    persist_import(preview, seeded_db)
    seeded_db.commit()

    entry = _entry(seeded_db, "DNS")
    count = seeded_db.scalar(
        select(func.count(ScheduleEntryResource.resource_id))
        .where(ScheduleEntryResource.schedule_entry_id == entry.id)
    )
    assert count == 2, f"Expected 2 join-table rows, got {count}"


# ---------------------------------------------------------------------------
# Test 3: Singular resource_id FK set to first resource for backwards compat
# ---------------------------------------------------------------------------

def test_multi_resource_singular_fk_is_first(seeded_db):
    """resource_id on ScheduleEntry should be the first resource (CA3)."""
    preview = _preview("TSP", "wednesday", ["S4", "S5"],
                       room="CA3, FDC", resource_codes=["CA3", "FDC"])
    persist_import(preview, seeded_db)
    seeded_db.commit()

    entry = _entry(seeded_db, "TSP")
    ca3 = seeded_db.scalar(select(Resource).where(Resource.name == "CA3"))
    assert entry.resource_id == ca3.id, "resource_id should point to first resource CA3"


# ---------------------------------------------------------------------------
# Test 4: Single-resource case unchanged (backwards compat)
# ---------------------------------------------------------------------------

def test_single_resource_backwards_compat(seeded_db):
    """Single resource: one Resource row, one join-table row, resource_id set."""
    preview = _preview("VK", "friday", ["S7", "S8"],
                       room="Lab1A", resource_codes=["Lab1A"])
    persist_import(preview, seeded_db)
    seeded_db.commit()

    # No spurious composite name
    assert seeded_db.scalar(
        select(func.count(Resource.id)).where(Resource.name.like("Lab1A,%"))
    ) == 0

    lab1a = seeded_db.scalar(select(Resource).where(Resource.name == "Lab1A"))
    assert lab1a is not None, "Lab1A resource missing"

    entry = _entry(seeded_db, "VK")
    assert entry.resource_id == lab1a.id, "resource_id not set for single-resource case"

    count = seeded_db.scalar(
        select(func.count(ScheduleEntryResource.resource_id))
        .where(ScheduleEntryResource.schedule_entry_id == entry.id)
    )
    assert count == 1, f"Expected 1 join-table row, got {count}"


# ---------------------------------------------------------------------------
# Test 5: Legacy fallback — room set but resource_codes=[] (old XLSX / manual)
# ---------------------------------------------------------------------------

def test_legacy_room_fallback(seeded_db):
    """If resource_codes is empty but room is set, treat room as single code."""
    preview = _preview("RR", "monday", ["S1"],
                       room="CA1", resource_codes=[])
    persist_import(preview, seeded_db)
    seeded_db.commit()

    ca1 = seeded_db.scalar(select(Resource).where(Resource.name == "CA1"))
    assert ca1 is not None, "CA1 should be created via room fallback"

    entry = _entry(seeded_db, "RR")
    assert entry.resource_id == ca1.id, "resource_id not set via legacy fallback"


# ---------------------------------------------------------------------------
# Test 6: Three resources in one group
# ---------------------------------------------------------------------------

def test_three_resource_group(seeded_db):
    """Lab1B, CA3, FDC → 3 separate rows, 3 join-table entries, no composite."""
    preview = _preview("TSP", "wednesday", ["S4", "S5"],
                       room="Lab1B, CA3, FDC",
                       resource_codes=["Lab1B", "CA3", "FDC"])
    persist_import(preview, seeded_db)
    seeded_db.commit()

    # Composite name must not exist
    assert seeded_db.scalar(
        select(func.count(Resource.id)).where(Resource.name == "Lab1B, CA3, FDC")
    ) == 0

    for name in ["Lab1B", "CA3", "FDC"]:
        r = seeded_db.scalar(select(Resource).where(Resource.name == name))
        assert r is not None, f"Resource '{name}' not created"

    entry = _entry(seeded_db, "TSP")
    count = seeded_db.scalar(
        select(func.count(ScheduleEntryResource.resource_id))
        .where(ScheduleEntryResource.schedule_entry_id == entry.id)
    )
    assert count == 3, f"Expected 3 join-table rows, got {count}"
