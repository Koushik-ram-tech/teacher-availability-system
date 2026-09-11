"""
Tests for the canonical domain model and converters.

This test suite verifies:
1. SourceLocation construction and display for XLSX, DOCX, MANUAL
2. ValidationIssue construction with severity levels
3. CanonicalTimetable construction and utilities
4. Conversion between ImportPreview and CanonicalTimetable
5. Preservation of source location traceability
"""
from __future__ import annotations

import uuid

import pytest

from app.domain.converters import canonical_to_import_preview, import_preview_to_canonical
from app.domain.timetable import (
    ActivitySlotRange,
    CanonicalTimetable,
    ScheduleActivity,
    SourceLocation,
    TeacherIdentity,
    ValidationIssue,
    ValidationSeverity,
)
from app.schemas.imports import ImportPreview, ScheduleImportRow, TeacherImportRow


class TestSourceLocation:
    """Test SourceLocation for XLSX, DOCX, and MANUAL sources."""

    def test_xlsx_source_location(self):
        """Test XLSX source location with sheet, row, and field."""
        loc = SourceLocation(
            source_type="XLSX",
            sheet_name="Schedule",
            row_number=12,
            field_name="teacher_acronym",
        )
        assert loc.source_type == "XLSX"
        assert loc.sheet_name == "Schedule"
        assert loc.row_number == 12
        assert loc.field_name == "teacher_acronym"
        display = loc.to_display()
        assert "XLSX" in display
        assert "Schedule" in display
        assert "12" in display
        assert "teacher_acronym" in display

    def test_docx_source_location(self):
        """Test DOCX source location with table, row, and column."""
        loc = SourceLocation(
            source_type="DOCX",
            table_index=2,
            table_row=5,
            table_col=3,
        )
        assert loc.source_type == "DOCX"
        assert loc.table_index == 2
        assert loc.table_row == 5
        assert loc.table_col == 3
        display = loc.to_display()
        assert "DOCX" in display
        assert "table 2" in display
        assert "row 5" in display
        assert "col 3" in display

    def test_manual_source_location(self):
        """Test MANUAL source location with display context."""
        loc = SourceLocation(
            source_type="MANUAL",
            display_context="Edited by Dr. Smith on 2026-09-10",
        )
        assert loc.source_type == "MANUAL"
        display = loc.to_display()
        assert "Dr. Smith" in display

    def test_source_location_immutability(self):
        """Test that SourceLocation is frozen."""
        loc = SourceLocation(source_type="XLSX", sheet_name="Test")
        with pytest.raises(Exception):  # FrozenInstanceError
            loc.sheet_name = "Modified"  # type: ignore[misc]


class TestValidationIssue:
    """Test ValidationIssue construction and serialization."""

    def test_error_issue(self):
        """Test ERROR-level validation issue."""
        issue = ValidationIssue(
            severity=ValidationSeverity.ERROR,
            code="SLOT_OVERLAP",
            message="Slot S4 overlaps with existing entry",
            affected_entities={"teacher": "RAJ", "day": "monday", "slot": "S4"},
            suggestion="Remove the conflicting entry or choose a different slot",
        )
        assert issue.severity == ValidationSeverity.ERROR
        assert issue.code == "SLOT_OVERLAP"
        assert "overlaps" in issue.message
        assert issue.affected_entities["teacher"] == "RAJ"
        assert issue.suggestion is not None

    def test_warning_issue(self):
        """Test WARNING-level validation issue."""
        issue = ValidationIssue(
            severity=ValidationSeverity.WARNING,
            code="MISSING_ROOM",
            message="Room not specified for LAB activity",
        )
        assert issue.severity == ValidationSeverity.WARNING
        assert issue.code == "MISSING_ROOM"

    def test_issue_with_source_locations(self):
        """Test validation issue with multiple source locations."""
        loc1 = SourceLocation(source_type="XLSX", sheet_name="Schedule", row_number=10)
        loc2 = SourceLocation(source_type="XLSX", sheet_name="Schedule", row_number=15)
        
        issue = ValidationIssue(
            severity=ValidationSeverity.ERROR,
            code="DUPLICATE_SLOT",
            message="Same slot assigned twice",
            source_locations=[loc1, loc2],
        )
        assert len(issue.source_locations) == 2
        
        issue_dict = issue.to_dict()
        assert issue_dict["severity"] == "ERROR"
        assert len(issue_dict["source_locations"]) == 2


class TestTeacherIdentity:
    """Test TeacherIdentity construction."""

    def test_create_new_teacher(self):
        """Test creating a new teacher identity."""
        teacher = TeacherIdentity(
            acronym="RAJ",
            name="Dr. Rajesh Kumar",
            level="PG",
            program_name="MCA",
            semester=1,
            department="Computer Science",
            action="CREATE",
        )
        assert teacher.acronym == "RAJ"
        assert teacher.name == "Dr. Rajesh Kumar"
        assert teacher.action == "CREATE"
        assert teacher.resolved_teacher_id is None
        assert len(teacher.issues) == 0

    def test_reuse_existing_teacher(self):
        """Test reusing an existing teacher."""
        teacher_id = uuid.uuid4()
        teacher = TeacherIdentity(
            acronym="SKR",
            name="Sahana KR",
            level="UG",
            program_name="BCA",
            semester=3,
            department="Computer Science",
            resolved_teacher_id=teacher_id,
            action="REUSE",
        )
        assert teacher.action == "REUSE"
        assert teacher.resolved_teacher_id == teacher_id


class TestCanonicalTimetable:
    """Test CanonicalTimetable construction and utilities."""

    def test_empty_timetable(self):
        """Test creating an empty timetable."""
        timetable = CanonicalTimetable(
            import_id="test-123",
            academic_year="2026-2027",
        )
        assert timetable.import_id == "test-123"
        assert timetable.academic_year == "2026-2027"
        assert len(timetable.teachers) == 0
        assert len(timetable.activities) == 0
        assert not timetable.has_errors()

    def test_timetable_with_teachers(self):
        """Test timetable with multiple teachers."""
        teacher1 = TeacherIdentity(
            acronym="RAJ",
            name="Dr. Rajesh Kumar",
            level="PG",
            program_name="MCA",
            semester=1,
            department="Computer Science",
        )
        teacher2 = TeacherIdentity(
            acronym="SKR",
            name="Sahana KR",
            level="UG",
            program_name="BCA",
            semester=3,
            department="Computer Science",
        )
        
        timetable = CanonicalTimetable(
            import_id="test-456",
            academic_year="2026-2027",
            teachers=[teacher1, teacher2],
        )
        
        assert len(timetable.teachers) == 2
        assert timetable.get_teacher("RAJ") == teacher1
        assert timetable.get_teacher("SKR") == teacher2
        assert timetable.get_teacher("UNKNOWN") is None

    def test_activity_with_two_slots(self):
        """Test activity spanning two consecutive slots (LAB)."""
        source_loc = SourceLocation(
            source_type="XLSX",
            sheet_name="Schedule",
            row_number=15,
        )
        
        slot_range = ActivitySlotRange(
            day_of_week=4,  # Thursday
            slot_codes=["S4", "S5"],
            source_location=source_loc,
        )
        
        activity = ScheduleActivity(
            teacher_acronym="RAJ",
            entry_type="LAB",
            subject_or_activity="DBMS Lab",
            section="MCA-1A",
            room="LAB-3",
            notes="Bring laptops",
            slot_range=slot_range,
        )
        
        assert activity.entry_type == "LAB"
        assert len(activity.slot_range.slot_codes) == 2
        assert activity.slot_range.slot_codes == ["S4", "S5"]
        assert activity.slot_range.day_of_week == 4

    def test_timetable_with_activities(self):
        """Test timetable with multiple activities."""
        teacher = TeacherIdentity(
            acronym="RAJ",
            name="Dr. Rajesh Kumar",
            level="PG",
            program_name="MCA",
            semester=1,
            department="Computer Science",
        )
        
        activity1 = ScheduleActivity(
            teacher_acronym="RAJ",
            entry_type="CLASS",
            subject_or_activity="Data Structures",
            section="MCA-1A",
            room="CR-101",
            notes=None,
            slot_range=ActivitySlotRange(day_of_week=1, slot_codes=["S1"]),
        )
        
        activity2 = ScheduleActivity(
            teacher_acronym="RAJ",
            entry_type="LAB",
            subject_or_activity="DBMS Lab",
            section="MCA-1A",
            room="LAB-3",
            notes=None,
            slot_range=ActivitySlotRange(day_of_week=4, slot_codes=["S4", "S5"]),
        )
        
        timetable = CanonicalTimetable(
            import_id="test-789",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity1, activity2],
        )
        
        assert len(timetable.activities) == 2
        raj_activities = timetable.get_activities_by_teacher("RAJ")
        assert len(raj_activities) == 2

    def test_timetable_has_errors(self):
        """Test error detection in timetable."""
        error_issue = ValidationIssue(
            severity=ValidationSeverity.ERROR,
            code="TEST_ERROR",
            message="Test error",
        )
        
        warning_issue = ValidationIssue(
            severity=ValidationSeverity.WARNING,
            code="TEST_WARNING",
            message="Test warning",
        )
        
        # Timetable with only warnings
        timetable1 = CanonicalTimetable(
            import_id="test-1",
            academic_year="2026-2027",
            global_issues=[warning_issue],
        )
        assert not timetable1.has_errors()
        
        # Timetable with global error
        timetable2 = CanonicalTimetable(
            import_id="test-2",
            academic_year="2026-2027",
            global_issues=[error_issue],
        )
        assert timetable2.has_errors()
        
        # Timetable with teacher error
        teacher = TeacherIdentity(
            acronym="RAJ",
            name="Dr. Rajesh",
            level="PG",
            program_name="MCA",
            semester=1,
            department="CS",
            issues=[error_issue],
        )
        timetable3 = CanonicalTimetable(
            import_id="test-3",
            academic_year="2026-2027",
            teachers=[teacher],
        )
        assert timetable3.has_errors()


class TestConverters:
    """Test conversion between ImportPreview and CanonicalTimetable."""

    def test_import_preview_to_canonical_basic(self):
        """Test basic conversion from ImportPreview to CanonicalTimetable."""
        preview = ImportPreview(
            import_id="preview-123",
            academic_year="2026-2027",
            teachers=[
                TeacherImportRow(
                    row_ref="Teachers!2",
                    action="CREATE",
                    name="Dr. Rajesh Kumar",
                    acronym="RAJ",
                    level="PG",
                    program_name="MCA",
                    semester=1,
                    department="Computer Science",
                    warnings=["Missing phone number"],
                ),
            ],
            days={
                "monday": [
                    ScheduleImportRow(
                        row_refs=["Schedule!3"],
                        teacher_acronym="RAJ",
                        day="monday",
                        slot_ids=["S1"],
                        entry_type="CLASS",
                        subject_or_activity="Data Structures",
                        section="MCA-1A",
                        room="CR-101",
                        warnings=[],
                    ),
                ],
            },
            warnings=["Global warning example"],
            errors=[],
        )
        
        canonical = import_preview_to_canonical(preview)
        
        assert canonical.import_id == "preview-123"
        assert canonical.academic_year == "2026-2027"
        assert len(canonical.teachers) == 1
        assert canonical.teachers[0].acronym == "RAJ"
        assert len(canonical.teachers[0].issues) == 1
        assert canonical.teachers[0].issues[0].severity == ValidationSeverity.WARNING
        
        assert len(canonical.activities) == 1
        assert canonical.activities[0].teacher_acronym == "RAJ"
        assert canonical.activities[0].entry_type == "CLASS"
        assert canonical.activities[0].slot_range.day_of_week == 1  # Monday
        assert canonical.activities[0].slot_range.slot_codes == ["S1"]
        
        assert len(canonical.global_issues) == 1
        assert canonical.global_issues[0].severity == ValidationSeverity.WARNING

    def test_import_preview_to_canonical_lab_activity(self):
        """Test conversion with multi-slot LAB activity."""
        preview = ImportPreview(
            import_id="preview-456",
            academic_year="2026-2027",
            teachers=[
                TeacherImportRow(
                    row_ref="Teachers!2",
                    action="CREATE",
                    name="Dr. Rajesh Kumar",
                    acronym="RAJ",
                    level="PG",
                    program_name="MCA",
                    semester=1,
                    department="Computer Science",
                ),
            ],
            days={
                "thursday": [
                    ScheduleImportRow(
                        row_refs=["Schedule!15"],
                        teacher_acronym="RAJ",
                        day="thursday",
                        slot_ids=["S4", "S5"],
                        entry_type="LAB",
                        subject_or_activity="DBMS Lab",
                        section="MCA-1A",
                        room="LAB-3",
                        notes="Bring laptops",
                        warnings=[],
                    ),
                ],
            },
        )
        
        canonical = import_preview_to_canonical(preview)
        
        assert len(canonical.activities) == 1
        activity = canonical.activities[0]
        assert activity.entry_type == "LAB"
        assert len(activity.slot_range.slot_codes) == 2
        assert activity.slot_range.slot_codes == ["S4", "S5"]
        assert activity.slot_range.day_of_week == 4  # Thursday
        assert activity.slot_range.source_location is not None
        assert activity.slot_range.source_location.row_number == 15

    def test_canonical_to_import_preview_roundtrip(self):
        """Test roundtrip conversion: ImportPreview → Canonical → ImportPreview."""
        original_preview = ImportPreview(
            import_id="roundtrip-789",
            academic_year="2026-2027",
            teachers=[
                TeacherImportRow(
                    row_ref="Teachers!2",
                    action="CREATE",
                    name="Dr. Rajesh Kumar",
                    acronym="RAJ",
                    level="PG",
                    program_name="MCA",
                    semester=1,
                    department="Computer Science",
                ),
            ],
            days={
                "monday": [
                    ScheduleImportRow(
                        row_refs=["Schedule!3"],
                        teacher_acronym="RAJ",
                        day="monday",
                        slot_ids=["S1"],
                        entry_type="CLASS",
                        subject_or_activity="Data Structures",
                        section="MCA-1A",
                        room="CR-101",
                    ),
                ],
            },
        )
        
        # Forward conversion
        canonical = import_preview_to_canonical(original_preview)
        
        # Reverse conversion
        reconstructed_preview = canonical_to_import_preview(canonical)
        
        assert reconstructed_preview.import_id == original_preview.import_id
        assert reconstructed_preview.academic_year == original_preview.academic_year
        assert len(reconstructed_preview.teachers) == len(original_preview.teachers)
        assert reconstructed_preview.teachers[0].acronym == "RAJ"
        assert len(reconstructed_preview.days) == len(original_preview.days)
        assert "monday" in reconstructed_preview.days
        assert len(reconstructed_preview.days["monday"]) == 1
        assert reconstructed_preview.days["monday"][0].teacher_acronym == "RAJ"

    def test_canonical_with_errors_preserved(self):
        """Test that errors in ImportPreview are preserved in canonical conversion."""
        preview = ImportPreview(
            import_id="error-test",
            academic_year="2026-2027",
            errors=["Critical error: duplicate teacher"],
        )
        
        canonical = import_preview_to_canonical(preview)
        
        assert len(canonical.global_issues) == 1
        assert canonical.global_issues[0].severity == ValidationSeverity.ERROR
        assert canonical.has_errors()
        
        # Convert back
        reconstructed = canonical_to_import_preview(canonical)
        assert len(reconstructed.errors) == 1
