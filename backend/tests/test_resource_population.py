"""Tests for automatic resource population during import confirmation.

Verifies that resource creation/linking happens automatically during
the normal import → confirm workflow, eliminating the need for manual
populate_resources.py execution.
"""
import pytest
from sqlalchemy import select, func

from app.models.models import Resource, ResourceAlias, ScheduleEntry, Timetable
from app.services.resource_populator import (
    classify_resource_type,
    find_or_create_resource,
    create_alias_if_needed,
)
from app.domain.resources import normalize_resource_name


class TestResourceClassification:
    """Test resource type classification rules."""

    def test_lab_classification(self):
        """Lab patterns correctly classified."""
        assert classify_resource_type("Lab1A") == "LAB"
        assert classify_resource_type("Lab 1A") == "LAB"
        assert classify_resource_type("lab1a") == "LAB"
        assert classify_resource_type("Lab1B") == "LAB"

    def test_fdc_classification(self):
        """FDC classified as LAB."""
        assert classify_resource_type("FDC") == "LAB"
        assert classify_resource_type("fdc") == "LAB"

    def test_classroom_classification(self):
        """CA + digit classified as CLASSROOM."""
        assert classify_resource_type("CA1") == "CLASSROOM"
        assert classify_resource_type("CA2") == "CLASSROOM"
        assert classify_resource_type("CA3") == "CLASSROOM"
        assert classify_resource_type("ca1") == "CLASSROOM"

    def test_other_classification(self):
        """Unknown patterns classified as OTHER."""
        assert classify_resource_type("Auditorium") == "OTHER"
        assert classify_resource_type("Unknown") == "OTHER"


class TestResourceNormalization:
    """Test resource name normalization."""

    def test_normalization_lowercase(self):
        """Normalization converts to lowercase."""
        assert normalize_resource_name("LAB1A") == "lab1a"
        assert normalize_resource_name("Lab1A") == "lab1a"

    def test_normalization_whitespace(self):
        """Normalization collapses whitespace."""
        assert normalize_resource_name("Lab 1A") == "lab 1a"
        assert normalize_resource_name("Lab  1A") == "lab 1a"
        assert normalize_resource_name("  Lab 1A  ") == "lab 1a"

    def test_lab1a_variations_normalize_differently(self):
        """Lab1A and Lab 1A have different normalized forms (space matters)."""
        # This is intentional - aliases bridge the gap
        assert normalize_resource_name("Lab1A") == "lab1a"
        assert normalize_resource_name("Lab 1A") == "lab 1a"
        assert normalize_resource_name("Lab1A") != normalize_resource_name("Lab 1A")


class TestResourceCreation:
    """Test resource creation and reuse."""

    def test_create_resource(self, db):
        """Creating a resource works."""
        resource = find_or_create_resource(
            db=db,
            name="Lab1A",
            resource_type="LAB",
            department=None,
        )

        assert resource.id is not None
        assert resource.name == "Lab1A"
        assert resource.normalized_name == "lab1a"
        assert resource.resource_type == "LAB"
        assert resource.department is None
        assert resource.is_active is True

    def test_reuse_existing_resource(self, db):
        """Finding existing resource by normalized name."""
        # Create first
        resource1 = find_or_create_resource(
            db=db,
            name="Lab1A",
            resource_type="LAB",
            department=None,
        )
        db.flush()

        # Try to create again - should reuse
        resource2 = find_or_create_resource(
            db=db,
            name="Lab1A",
            resource_type="LAB",
            department=None,
        )

        assert resource1.id == resource2.id

    def test_idempotent_creation(self, db):
        """Multiple calls with same name reuse resource."""
        resources = []
        for _ in range(3):
            r = find_or_create_resource(
                db=db,
                name="CA1",
                resource_type="CLASSROOM",
                department=None,
            )
            resources.append(r.id)
            db.flush()

        # All should be the same resource
        assert len(set(resources)) == 1


class TestResourceAliases:
    """Test resource alias creation."""

    def test_create_alias(self, db):
        """Creating an alias works."""
        resource = find_or_create_resource(
            db=db,
            name="Lab1A",
            resource_type="LAB",
            department=None,
        )
        db.flush()

        alias = create_alias_if_needed(db, resource, "Lab 1A")

        assert alias is not None
        assert alias.resource_id == resource.id
        assert alias.alias == "Lab 1A"
        assert alias.normalized_alias == "lab 1a"

    def test_no_alias_for_same_normalized(self, db):
        """No alias created if it matches resource normalized name."""
        resource = find_or_create_resource(
            db=db,
            name="Lab1A",
            resource_type="LAB",
            department=None,
        )
        db.flush()

        # Try to create alias with same normalized form
        alias = create_alias_if_needed(db, resource, "lab1a")

        assert alias is None

    def test_lab1a_lab_1a_aliasing(self, db):
        """Lab1A and Lab 1A can be aliases of each other."""
        # Create with no space
        resource = find_or_create_resource(
            db=db,
            name="Lab1A",
            resource_type="LAB",
            department=None,
        )
        db.flush()

        # Create alias with space
        alias = create_alias_if_needed(db, resource, "Lab 1A")
        db.flush()  # Ensure alias is persisted

        assert alias is not None
        assert alias.normalized_alias == "lab 1a"

        # Both should resolve to same resource
        from app.services.availability import AvailabilityService
        found1 = AvailabilityService.find_resource_by_code(db, "Lab1A")
        found2 = AvailabilityService.find_resource_by_code(db, "Lab 1A")

        assert found1 is not None
        assert found2 is not None
        assert found1.id == found2.id


class TestResourceLinkage:
    """Test schedule_entry → resource linking."""

    def test_missing_room_leaves_null(self, db):
        """Schedule entry with no room has NULL resource_id."""
        # This would be tested via full import, but the logic is:
        # if entry_in.room is None or empty -> resource_id remains NULL
        pass  # Covered by integration tests


class TestAutomaticResourcePopulation:
    """Test that confirmation automatically populates resources."""

    def test_resource_linking_during_persistence(self, db):
        """Test that persist_import links resources during schedule entry creation."""
        # This tests the core logic without requiring full API flow
        from app.services.excel_import.persister import persist_import
        from app.schemas.imports import ImportPreview, TeacherImportRow, ScheduleImportRow
        from app.models.models import Program, Resource, ScheduleEntry, TimeSlot
        from sqlalchemy import select
        import uuid
        from datetime import time

        # Seed time slots
        for i, code in enumerate(["S1", "S2"], start=1):
            slot = TimeSlot(
                id=uuid.uuid4(),
                code=code,
                start_time=time(8, 0),
                end_time=time(9, 0),
                sequence=i,
                is_active=True,
            )
            db.add(slot)

        # Create a test program
        prog = Program(id=uuid.uuid4(), name="Test Program", level="PG", is_active=True)
        db.add(prog)
        db.commit()

        # Create import preview with room assignments
        preview = ImportPreview(
            import_id=str(uuid.uuid4()),
            academic_year="2099-2100",
            teachers=[
                TeacherImportRow(
                    row_ref="Test!1",
                    action="CREATE",
                    name="Test Teacher",
                    acronym="TSTRES",
                    level="PG",
                    program_name="Test Program",
                    semester=1,
                    department="Test Dept",
                    resolved_teacher_id=None,
                    warnings=[],
                )
            ],
            days={
                "monday": [
                    ScheduleImportRow(
                        row_refs=["Test!2"],
                        teacher_acronym="TSTRES",
                        day="monday",
                        entry_type="LAB",
                        slot_ids=["S1", "S2"],
                        subject_or_activity="Test Lab",
                        section="I-A",
                        room="TestLab1",  # Explicit room assignment
                        notes=None,
                        warnings=[],
                    )
                ]
            },
            warnings=[],
            errors=[],
        )

        # Check resources before
        resources_before = db.execute(select(func.count(Resource.id))).scalar()

        # Persist import
        result = persist_import(preview, db)
        db.commit()

        # Verify resource was created
        resources_after = db.execute(select(func.count(Resource.id))).scalar()
        assert resources_after > resources_before, "Resource should have been created"

        # Verify TestLab1 exists
        testlab = db.execute(
            select(Resource).where(Resource.normalized_name == "testlab1")
        ).scalar_one_or_none()
        assert testlab is not None, "TestLab1 resource should exist"

        # Verify schedule entry is linked
        entries = db.execute(
            select(ScheduleEntry)
            .where(ScheduleEntry.room == "TestLab1")
        ).scalars().all()
        assert len(entries) > 0, "Should have schedule entries"
        assert all(e.resource_id == testlab.id for e in entries), "All entries should link to resource"

        # Verify stats
        assert result["resources_created"] >= 1
        assert result["entries_linked"] >= 1

    def test_missing_room_leaves_null(self, db):
        """Test that entries without room don't get resource_id."""
        from app.services.excel_import.persister import persist_import
        from app.schemas.imports import ImportPreview, TeacherImportRow, ScheduleImportRow
        from app.models.models import Program, ScheduleEntry, TimeSlot
        from sqlalchemy import select
        import uuid
        from datetime import time

        # Seed time slots
        slot = TimeSlot(
            id=uuid.uuid4(),
            code="S1",
            start_time=time(8, 0),
            end_time=time(9, 0),
            sequence=1,
            is_active=True,
        )
        db.add(slot)

        # Create test program
        prog = Program(id=uuid.uuid4(), name="Test Program 2", level="PG", is_active=True)
        db.add(prog)
        db.commit()

        # Create import with NO room
        preview = ImportPreview(
            import_id=str(uuid.uuid4()),
            academic_year="2099-2100",
            teachers=[
                TeacherImportRow(
                    row_ref="Test!1",
                    action="CREATE",
                    name="Test Teacher 2",
                    acronym="TST2",
                    level="PG",
                    program_name="Test Program 2",
                    semester=1,
                    department="Test Dept",
                    resolved_teacher_id=None,
                    warnings=[],
                )
            ],
            days={
                "monday": [
                    ScheduleImportRow(
                        row_refs=["Test!2"],
                        teacher_acronym="TST2",
                        day="monday",
                        entry_type="CLASS",
                        slot_ids=["S1"],
                        subject_or_activity="Test Class",
                        section="I-A",
                        room=None,  # NO room assignment
                        notes=None,
                        warnings=[],
                    )
                ]
            },
            warnings=[],
            errors=[],
        )

        # Persist
        result = persist_import(preview, db)
        db.commit()

        # Verify no resources were linked (room was None)
        assert result["entries_linked"] == 0, "No entries should be linked when room is None"

        # Verify schedule entry has NULL resource_id
        entries = db.execute(
            select(ScheduleEntry)
            .where(ScheduleEntry.subject_or_activity == "Test Class")
        ).scalars().all()
        assert len(entries) > 0
        assert all(e.resource_id is None for e in entries), "resource_id should be NULL"


class TestResourceIntegrationScenarios:
    """Integration scenarios for resource population."""

    def test_no_duplicate_resources_on_retry(self, db):
        """Creating same resource twice doesn't duplicate."""
        # First creation
        r1 = find_or_create_resource(db, "Lab1A", "LAB", None)
        db.flush()
        count1 = db.execute(select(func.count(Resource.id))).scalar()

        # Second creation (simulating retry/re-import)
        r2 = find_or_create_resource(db, "Lab1A", "LAB", None)
        db.flush()
        count2 = db.execute(select(func.count(Resource.id))).scalar()

        assert r1.id == r2.id
        assert count1 == count2  # No new resource created
