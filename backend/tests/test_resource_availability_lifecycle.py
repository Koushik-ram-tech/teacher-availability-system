"""Regression coverage for import-scoped external resource confirmation."""
from __future__ import annotations

import uuid
from datetime import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import staging
from app.domain.converters import canonical_to_import_preview
from app.schemas.docx_imports import DOCXImportPreview, ResolvedActivity
from app.domain.timetable import (
    ActivitySlotRange,
    CanonicalTimetable,
    ScheduleActivity,
    TeacherIdentity,
)
from app.models.models import (
    Program,
    Resource,
    ResourceAllocation,
    ScheduleEntry,
    ScheduleEntryResource,
    ScheduleEntrySlot,
    Teacher,
    TimeSlot,
    Timetable,
)
from app.services.availability import AvailabilityService
from app.services.excel_import.persister import persist_import
from app.services.resource_populator import find_or_create_resource


ACADEMIC_YEAR = "2025-2026"


@pytest.fixture
def resource_db(db):
    program = Program(id=uuid.uuid4(), name="MCA", level="PG", is_active=True)
    db.add(program)
    for code, start, end, sequence in [
        ("S1", time(8, 0), time(8, 55), 1),
        ("S2", time(8, 55), time(9, 50), 2),
        ("S3", time(9, 50), time(10, 45), 3),
        ("S4", time(11, 15), time(12, 10), 4),
        ("S5", time(12, 10), time(13, 5), 5),
        ("S6", time(14, 0), time(14, 55), 6),
        ("S7", time(14, 55), time(15, 50), 7),
        ("S8", time(15, 50), time(16, 45), 8),
        ("S9", time(16, 45), time(17, 40), 9),
    ]:
        db.add(TimeSlot(
            id=uuid.uuid4(),
            code=code,
            start_time=start,
            end_time=end,
            sequence=sequence,
            is_active=True,
        ))
    db.commit()
    return program


def _external_canonical(import_id: str, activities: list[tuple[str, list[str]]]) -> CanonicalTimetable:
    external = TeacherIdentity(
        acronym="Ind*",
        name="Industry Participant",
        level="PG",
        program_name="MCA",
        semester=1,
        department="External",
        is_external=True,
    )
    return CanonicalTimetable(
        import_id=import_id,
        academic_year=ACADEMIC_YEAR,
        teachers=[external],
        activities=[
            ScheduleActivity(
                teacher_acronym="Ind*",
                entry_type="OTHER",
                subject_or_activity="External session",
                section=None,
                room="FDC",
                notes=None,
                slot_range=ActivitySlotRange(
                    day_of_week={"monday": 1, "tuesday": 2}[day],
                    slot_codes=slots,
                ),
                resource_codes=["FDC"],
                is_external=True,
            )
            for day, slots in activities
        ],
    )


def _stage_external(import_id: str, activities: list[tuple[str, list[str]]]) -> None:
    staging.stage_canonical(import_id, _external_canonical(import_id, activities))


def _resource(db, name: str = "FDC") -> Resource:
    resource = db.scalar(select(Resource).where(Resource.name == name))
    assert resource is not None
    return resource


def _allocation_statuses(db, import_id: str) -> list[str]:
    return db.scalars(
        select(ResourceAllocation.status).where(ResourceAllocation.source_import_id == import_id)
    ).all()


def _slot_id(db, code: str) -> uuid.UUID:
    slot = db.scalar(select(TimeSlot).where(TimeSlot.code == code))
    assert slot is not None
    return slot.id


def test_draft_external_allocation_is_not_visible(db, resource_db):
    import_id = str(uuid.uuid4())
    preview = canonical_to_import_preview(
        _external_canonical(import_id, [("monday", ["S6", "S7"])])
    )

    persist_import(preview, db)
    db.commit()

    assert _allocation_statuses(db, import_id) == ["DRAFT"]
    availability = AvailabilityService.get_resource_availability(
        db, _resource(db).id, ACADEMIC_YEAR, "monday"
    )
    assert availability is not None
    assert availability.days["monday"].slots["S6"].status == "FREE"
    assert availability.days["monday"].slots["S7"].status == "FREE"


def test_confirmation_exposes_fdc_monday_and_tuesday_occupancy(
    client: TestClient, db, resource_db
):
    import_id = str(uuid.uuid4())
    _stage_external(import_id, [("monday", ["S6", "S7"]), ("tuesday", ["S7", "S8"])])

    response = client.post(f"/api/v1/imports/{import_id}/confirm")

    assert response.status_code == 200, response.text
    assert _allocation_statuses(db, import_id) == ["CONFIRMED", "CONFIRMED"]
    availability = AvailabilityService.get_resource_availability(
        db, _resource(db).id, ACADEMIC_YEAR
    )
    assert availability is not None
    assert availability.days["monday"].slots["S6"].status == "OCCUPIED"
    assert availability.days["monday"].slots["S7"].status == "OCCUPIED"
    assert availability.days["tuesday"].slots["S7"].status == "OCCUPIED"
    assert availability.days["tuesday"].slots["S8"].status == "OCCUPIED"


def test_confirmation_promotes_only_current_import_allocations(
    client: TestClient, db, resource_db
):
    other_import_id = str(uuid.uuid4())
    persist_import(
        canonical_to_import_preview(
            _external_canonical(other_import_id, [("monday", ["S8"])])
        ),
        db,
    )
    db.commit()

    import_id = str(uuid.uuid4())
    persist_import(
        canonical_to_import_preview(
            _external_canonical(import_id, [("monday", ["S9"])])
        ),
        db,
    )
    db.commit()
    _stage_external(import_id, [("monday", ["S6", "S7"])])
    response = client.post(f"/api/v1/imports/{import_id}/confirm")

    assert response.status_code == 200, response.text
    assert _allocation_statuses(db, import_id) == ["CONFIRMED"]
    assert _allocation_statuses(db, other_import_id) == ["DRAFT"]
    current_allocation = db.scalar(
        select(ResourceAllocation).where(ResourceAllocation.source_import_id == import_id)
    )
    assert current_allocation is not None
    assert {link.time_slot.code for link in current_allocation.slot_links} == {"S6", "S7"}


def test_failed_confirmation_rolls_back_external_allocation_promotion(
    client: TestClient, db, resource_db, monkeypatch: pytest.MonkeyPatch
):
    import_id = str(uuid.uuid4())
    preview = canonical_to_import_preview(
        _external_canonical(import_id, [("monday", ["S6", "S7"])])
    )
    persist_import(preview, db)
    db.commit()
    _stage_external(import_id, [("monday", ["S6", "S7"])])

    def fail_commit() -> None:
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    response = client.post(f"/api/v1/imports/{import_id}/confirm")

    assert response.status_code == 500
    assert "CONFIRMED" not in _allocation_statuses(db, import_id)


def test_resource_conflict_blocks_external_allocation_confirmation(
    client: TestClient, db, resource_db
):
    existing_import_id = str(uuid.uuid4())
    existing_preview = canonical_to_import_preview(
        _external_canonical(existing_import_id, [("monday", ["S6"])])
    )
    persist_import(existing_preview, db)
    db.flush()
    existing = db.scalar(
        select(ResourceAllocation).where(
            ResourceAllocation.source_import_id == existing_import_id
        )
    )
    assert existing is not None
    existing.status = "CONFIRMED"
    db.commit()
    assert _allocation_statuses(db, existing_import_id) == ["CONFIRMED"]

    import_id = str(uuid.uuid4())
    _stage_external(import_id, [("monday", ["S6"])])
    response = client.post(f"/api/v1/imports/{import_id}/confirm")

    assert response.status_code == 422, response.text
    assert _allocation_statuses(db, import_id) == []
    assert "external participant" in response.json()["detail"]


def test_teacher_availability_is_unchanged_by_external_confirmation(
    client: TestClient, db, resource_db
):
    teacher_id = uuid.uuid4()
    timetable_id = uuid.uuid4()
    entry_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    db.add_all([
        Teacher(
            id=teacher_id,
            name="Existing Teacher",
            acronym="TT",
            level="PG",
            program_id=resource_db.id,
            semester=1,
            department="Test Dept",
            is_active=True,
        ),
        Timetable(
            id=timetable_id,
            teacher_id=teacher_id,
            academic_year=ACADEMIC_YEAR,
            status="CONFIRMED",
            source="test",
        ),
        Resource(
            id=resource_id,
            name="Lab1A",
            normalized_name="lab1a",
            resource_type="LAB",
            department=None,
            is_active=True,
        ),
    ])
    db.flush()
    entry = ScheduleEntry(
        id=entry_id,
        timetable_id=timetable_id,
        day_of_week=1,
        entry_type="CLASS",
        subject_or_activity="Existing class",
        section="I-A",
        room="Lab1A",
    )
    db.add(entry)
    db.flush()
    db.add_all([
        ScheduleEntrySlot(schedule_entry_id=entry_id, time_slot_id=_slot_id(db, "S1")),
        ScheduleEntryResource(schedule_entry_id=entry_id, resource_id=resource_id),
    ])
    db.commit()

    external_teacher_ids_before = set(db.scalars(
        select(Teacher.id).where(Teacher.acronym == "Ind*")
    ).all())
    before = AvailabilityService.get_teacher_availability(
        db, teacher_id, ACADEMIC_YEAR, "monday"
    )
    import_id = str(uuid.uuid4())
    _stage_external(import_id, [("monday", ["S6", "S7"])])
    response = client.post(f"/api/v1/imports/{import_id}/confirm")
    after = AvailabilityService.get_teacher_availability(
        db, teacher_id, ACADEMIC_YEAR, "monday"
    )

    assert response.status_code == 200, response.text
    assert before is not None and after is not None
    external_teacher_ids_after = set(db.scalars(
        select(Teacher.id).where(Teacher.acronym == "Ind*")
    ).all())
    assert external_teacher_ids_after == external_teacher_ids_before
    assert {code: slot.status for code, slot in before.days["monday"].slots.items()} == {
        code: slot.status for code, slot in after.days["monday"].slots.items()
    }
    assert after.days["monday"].slots["S1"].status == "OCCUPIED"


def test_resource_catalog_filters_only_same_day_overlapping_slots(
    client: TestClient, db, resource_db
):
    fdc = Resource(
        id=uuid.uuid4(),
        name="FDC",
        normalized_name="fdc",
        resource_type="OTHER",
        department=None,
        is_active=True,
    )
    ca1 = Resource(
        id=uuid.uuid4(),
        name="CA1",
        normalized_name="ca1",
        resource_type="CLASSROOM",
        department=None,
        is_active=True,
    )
    ca2 = Resource(
        id=uuid.uuid4(),
        name="CA2",
        normalized_name="ca2",
        resource_type="CLASSROOM",
        department="Computer Applications",
        is_active=True,
    )
    teacher = Teacher(
        id=uuid.uuid4(),
        name="Catalog Test Teacher",
        acronym="CAT",
        level="PG",
        program_id=resource_db.id,
        semester=1,
        department="Computer Applications",
        is_active=True,
    )
    timetable = Timetable(
        id=uuid.uuid4(),
        teacher_id=teacher.id,
        academic_year=ACADEMIC_YEAR,
        status="CONFIRMED",
        source="test",
    )
    entry = ScheduleEntry(
        id=uuid.uuid4(),
        timetable_id=timetable.id,
        day_of_week=4,
        entry_type="OTHER",
        subject_or_activity="Placement",
        section="I-A",
        room="CA1",
    )
    db.add_all([fdc, ca1, ca2, teacher, timetable, entry])
    db.flush()
    db.add_all([
        ScheduleEntryResource(schedule_entry_id=entry.id, resource_id=ca1.id),
        ScheduleEntrySlot(schedule_entry_id=entry.id, time_slot_id=_slot_id(db, "S6")),
    ])
    db.commit()

    thursday = client.get(
        "/api/v1/availability/resources/catalog",
        params={
            "department": "Computer Applications",
            "academic_year": ACADEMIC_YEAR,
            "day": "thursday",
            "slots": "S6,S7",
        },
    )
    friday = client.get(
        "/api/v1/availability/resources/catalog",
        params={
            "department": "Computer Applications",
            "academic_year": ACADEMIC_YEAR,
            "day": "friday",
            "slots": "S6,S7",
        },
    )

    thursday_codes = {item["code"] for item in thursday.json()}
    friday_codes = {item["code"] for item in friday.json()}
    assert thursday.status_code == friday.status_code == 200
    assert "CA1" not in thursday_codes
    assert "CA1" in friday_codes
    assert "CA2" in thursday_codes
    assert "FDC" in thursday_codes


def test_resource_persistence_reuses_existing_alias(db, resource_db):
    canonical = Resource(
        id=uuid.uuid4(),
        name="LAB 1A",
        normalized_name="lab 1a",
        resource_type="LAB",
        department=None,
        is_active=True,
    )
    db.add(canonical)
    db.flush()
    from app.models.models import ResourceAlias

    db.add(ResourceAlias(
        id=uuid.uuid4(),
        resource_id=canonical.id,
        alias="LAB1A",
        normalized_alias="lab1a",
    ))
    db.flush()

    resolved = find_or_create_resource(db, "Lab1A", "LAB", department=None)

    assert resolved.id == canonical.id


def test_docx_confirmation_confirms_faculty_and_external_occupancy(
    client: TestClient, db, resource_db
):
    import_id = str(uuid.uuid4())
    staging.stage_docx_preview(
        import_id,
        DOCXImportPreview(
            import_id=import_id,
            filename="test.docx",
            academic_year=ACADEMIC_YEAR,
            department="Computer Applications",
            parser_status="COMPLETE",
            total_blocks=2,
            resolved_count=2,
            unresolved_count=0,
            resolved_activities=[
                ResolvedActivity(
                    day="monday",
                    section="I-A",
                    slots=["S1"],
                    teacher_acronym="SU",
                    subject_or_activity="Database Systems",
                    resource_code="CA1",
                    resource_codes=["CA1"],
                    source_location="DOCX: row 1",
                    source_cell_text="DBMS (SU) CA1",
                    group_index=0,
                ),
                ResolvedActivity(
                    day="monday",
                    section="I-A",
                    slots=["S6", "S7"],
                    teacher_acronym="",
                    subject_or_activity="Industry session",
                    resource_code="FDC",
                    resource_codes=["FDC"],
                    source_location="DOCX: row 2",
                    source_cell_text="Industry (Ind*) FDC",
                    group_index=0,
                ),
            ],
        ),
    )

    response = client.post(f"/api/v1/imports/{import_id}/confirm")

    assert response.status_code == 200, response.text
    timetable = db.scalar(select(Timetable).where(Timetable.academic_year == ACADEMIC_YEAR))
    assert timetable is not None
    assert timetable.status == "CONFIRMED"
    assert db.scalar(select(Teacher).where(Teacher.acronym == "SU")) is not None
    assert db.scalar(select(Teacher).where(Teacher.acronym == "Ind*")) is None
    assert _allocation_statuses(db, import_id) == ["CONFIRMED"]


def test_real_mca_docx_single_confirmation_resource_acceptance(
    client: TestClient, db, resource_db
):
    sample_path = Path(__file__).resolve().parents[2] / "sample_files" / "MCA Timetable-2026-Odd V7.docx"
    with sample_path.open("rb") as sample_file:
        upload_response = client.post(
            "/api/v1/imports/docx",
            files={
                "file": (
                    sample_path.name,
                    sample_file,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"academic_year": "2026-2027", "department": "Computer Applications"},
        )

    assert upload_response.status_code == 200, upload_response.text
    preview = upload_response.json()
    assert preview["unresolved_count"] == 4
    import_id = preview["import_id"]

    resource_by_section = {
        "I-A": "CA1",
        "I-B": "CA2",
        "III -A": "Lab1A",
        "III-B": "Lab1B",
    }
    remaining = 4
    while remaining:
        block = next(
            block for block in preview["unresolved_blocks"]
            if block["resolution_required"]
        )
        resource_code = resource_by_section[block["section"]]
        resolve_response = client.post(
            f"/api/v1/imports/{import_id}/resolve",
            json={
                "resolutions": [{
                    "block_id": block["block_id"],
                    "selected_activity": block["activity_candidates"][0]["code"],
                    "selected_teacher": None,
                    "selected_resource": resource_code,
                    "entry_type": "OTHER",
                }]
            },
        )
        assert resolve_response.status_code == 200, resolve_response.text
        remaining -= 1
        assert resolve_response.json()["remaining_unresolved"] == remaining
        preview["unresolved_blocks"] = [
            unresolved for unresolved in preview["unresolved_blocks"]
            if unresolved["block_id"] != block["block_id"]
        ]

    staged_preview = staging.get_docx_preview(import_id)
    assert staged_preview is not None
    resolved_placements = {
        activity.section: activity.resource_codes
        for activity in staged_preview.resolved_activities
        if activity.subject_or_activity == "Placement"
    }
    assert resolved_placements == {
        "I-A": ["CA1"],
        "I-B": ["CA2"],
        "III -A": ["Lab1A"],
        "III-B": ["Lab1B"],
    }

    confirm_response = client.post(f"/api/v1/imports/{import_id}/confirm")
    assert confirm_response.status_code == 200, confirm_response.text

    timetables = db.scalars(
        select(Timetable).where(Timetable.academic_year == "2026-2027")
    ).all()
    assert timetables
    assert all(timetable.status == "CONFIRMED" for timetable in timetables)
    assert _allocation_statuses(db, import_id)
    assert set(_allocation_statuses(db, import_id)) == {"CONFIRMED"}
    confirmed_placements = db.scalars(
        select(ResourceAllocation).where(
            ResourceAllocation.source_import_id == import_id,
            ResourceAllocation.day_of_week == 4,
            ResourceAllocation.subject_or_activity == "Placement",
        )
    ).all()
    selected_resource_ids = {
        section: AvailabilityService.find_resource_by_code(db, code).id
        for section, code in resource_by_section.items()
    }
    assert {
        allocation.section: [link.resource_id for link in allocation.resource_links]
        for allocation in confirmed_placements
    } == {
        section: [resource_id]
        for section, resource_id in selected_resource_ids.items()
    }

    expected_occupancy = {
        "FDC": {"monday": ["S6", "S7"], "tuesday": ["S7", "S8"], "wednesday": ["S4", "S5"]},
        "CA3": {"wednesday": ["S4", "S5"]},
        "Lab1A": {"friday": ["S7", "S8"], "saturday": ["S2", "S3"]},
        "Lab1B": {"saturday": ["S2", "S3"]},
        "CA1": {"friday": ["S7", "S8"]},
    }
    for resource_code, days in expected_occupancy.items():
        resource = AvailabilityService.find_resource_by_code(db, resource_code)
        assert resource is not None, f"{resource_code} resource was not persisted"
        linked_entries = db.scalars(
            select(ScheduleEntry)
            .join(ScheduleEntryResource)
            .where(ScheduleEntryResource.resource_id == resource.id)
        ).all()
        assert linked_entries, f"{resource_code} has no authoritative schedule_entry_resources links"
        if resource_code == "Lab1A":
            friday_codes = {
                slot.time_slot.code
                for entry in linked_entries
                if entry.day_of_week == 5 and entry.timetable.status == "CONFIRMED"
                for slot in entry.slot_links
            }
            assert {"S7", "S8"}.issubset(friday_codes), (
                "LAB1A Friday links have slots "
                f"{[(entry.day_of_week, entry.timetable.academic_year, entry.timetable.status, [slot.time_slot.code for slot in entry.slot_links]) for entry in linked_entries]}"
            )
        availability = AvailabilityService.get_resource_availability(
            db, resource.id, "2026-2027"
        )
        assert availability is not None
        for day, slots in days.items():
            for slot_code in slots:
                assert availability.days[day].slots[slot_code].status == "OCCUPIED", (
                    f"{resource_code} {day} {slot_code} should be occupied"
                )