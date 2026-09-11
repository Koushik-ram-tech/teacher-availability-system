"""
Tests for the canonical domain ValidationEngine.

This test suite verifies that the ValidationEngine correctly validates
CanonicalTimetable objects against domain rules and database state.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.domain.timetable import (
    ActivitySlotRange,
    CanonicalTimetable,
    ScheduleActivity,
    SourceLocation,
    TeacherIdentity,
    ValidationSeverity,
)
from app.domain.validation import validate_canonical
from app.models.models import Program


class TestValidationEngineBasics:
    """Test basic validation engine behavior."""

    def test_valid_empty_timetable(self, db: Session):
        """Empty timetable with no teachers or activities should be valid."""
        timetable = CanonicalTimetable(
            import_id="test-empty",
            academic_year="2026-2027",
        )
        
        validated = validate_canonical(timetable, db)
        
        assert not validated.has_errors()
        assert len(validated.global_issues) == 0

    def test_valid_single_activity(self, db: Session, program: dict):
        """Single valid activity should pass validation."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="Computer Science",
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="CLASS",
            subject_or_activity="Data Structures",
            section="MCA-1A",
            room="CR-101",
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=1,  # Monday
                slot_codes=["S1"],
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test-single",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert not validated.has_errors()

    def test_valid_lab_activity(self, db: Session, program: dict):
        """LAB spanning S4+S5 should be valid."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="Computer Science",
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="LAB",
            subject_or_activity="DBMS Lab",
            section="MCA-1A",
            room="LAB-3",
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=4,  # Thursday
                slot_codes=["S4", "S5"],
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test-lab",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert not validated.has_errors()


class TestTeacherValidation:
    """Test teacher validation rules."""

    def test_missing_teacher_name(self, db: Session, program: dict):
        """Missing teacher name should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="",  # Missing
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "TEACHER_MISSING_NAME" and issue.severity == ValidationSeverity.ERROR
            for issue in teacher.issues
        )

    def test_missing_teacher_acronym(self, db: Session, program: dict):
        """Missing teacher acronym should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="",  # Missing
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "TEACHER_MISSING_ACRONYM" and issue.severity == ValidationSeverity.ERROR
            for issue in teacher.issues
        )

    def test_invalid_teacher_level(self, db: Session, program: dict):
        """Invalid teacher level should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="INVALID",  # Should be UG or PG
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "TEACHER_INVALID_LEVEL" and issue.severity == ValidationSeverity.ERROR
            for issue in teacher.issues
        )

    def test_invalid_semester(self, db: Session, program: dict):
        """Invalid semester (<=0) should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=0,  # Invalid
            department="CS",
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "TEACHER_INVALID_SEMESTER" and issue.severity == ValidationSeverity.ERROR
            for issue in teacher.issues
        )

    def test_duplicate_teacher_identity(self, db: Session, program: dict):
        """Duplicate teacher acronym within timetable should produce ERROR."""
        teacher1 = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher 1",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        teacher2 = TeacherIdentity(
            acronym="TEST",  # Duplicate
            name="Test Teacher 2",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher1, teacher2],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "TEACHER_DUPLICATE_IDENTITY" and issue.severity == ValidationSeverity.ERROR
            for issue in teacher2.issues
        )

    def test_unknown_program(self, db: Session):
        """Teacher with unknown program should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name="UNKNOWN_PROGRAM",
            semester=1,
            department="CS",
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "TEACHER_UNKNOWN_PROGRAM" and issue.severity == ValidationSeverity.ERROR
            for issue in teacher.issues
        )


class TestScheduleValidation:
    """Test schedule activity validation rules."""

    def test_sunday_activity_rejected(self, db: Session, program: dict):
        """Sunday activity should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="CLASS",
            subject_or_activity="Test",
            section=None,
            room=None,
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=7,  # Sunday - invalid
                slot_codes=["S1"],
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "SCHEDULE_SUNDAY" and issue.severity == ValidationSeverity.ERROR
            for issue in activity.issues
        )

    def test_invalid_day(self, db: Session, program: dict):
        """Invalid day of week should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="CLASS",
            subject_or_activity="Test",
            section=None,
            room=None,
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=99,  # Invalid
                slot_codes=["S1"],
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "SCHEDULE_INVALID_DAY" and issue.severity == ValidationSeverity.ERROR
            for issue in activity.issues
        )

    def test_invalid_slot_code(self, db: Session, program: dict):
        """Invalid slot code should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="CLASS",
            subject_or_activity="Test",
            section=None,
            room=None,
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=1,
                slot_codes=["INVALID"],  # Invalid slot code
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "SCHEDULE_INVALID_SLOT" and issue.severity == ValidationSeverity.ERROR
            for issue in activity.issues
        )

    def test_lab_one_slot_rejected(self, db: Session, program: dict):
        """LAB with one slot should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="LAB",
            subject_or_activity="Test Lab",
            section=None,
            room=None,
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=1,
                slot_codes=["S1"],  # Only 1 slot - LAB needs 2
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "SCHEDULE_LAB_INVALID_DURATION" and issue.severity == ValidationSeverity.ERROR
            for issue in activity.issues
        )

    def test_lab_non_consecutive_rejected(self, db: Session, program: dict):
        """LAB with non-consecutive slots should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="LAB",
            subject_or_activity="Test Lab",
            section=None,
            room=None,
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=1,
                slot_codes=["S1", "S3"],  # Non-consecutive
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "SCHEDULE_LAB_NON_CONSECUTIVE" and issue.severity == ValidationSeverity.ERROR
            for issue in activity.issues
        )

    def test_lab_crossing_break_rejected(self, db: Session, program: dict):
        """LAB crossing break (S3+S4) should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="LAB",
            subject_or_activity="Test Lab",
            section=None,
            room=None,
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=1,
                slot_codes=["S3", "S4"],  # Crosses morning break
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "SCHEDULE_LAB_NON_CONSECUTIVE" and issue.severity == ValidationSeverity.ERROR
            for issue in activity.issues
        )

    def test_three_slot_activity_rejected(self, db: Session, program: dict):
        """Activity spanning 3+ slots should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="CLASS",
            subject_or_activity="Test",
            section=None,
            room=None,
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=1,
                slot_codes=["S1", "S2", "S3"],  # 3 slots - too many
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "SCHEDULE_INVALID_DURATION" and issue.severity == ValidationSeverity.ERROR
            for issue in activity.issues
        )


class TestOverlapDetection:
    """Test teacher overlap detection."""

    def test_teacher_overlap_detected(self, db: Session, program: dict):
        """Overlapping activities for same teacher should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        activity1 = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="CLASS",
            subject_or_activity="Subject A",
            section=None,
            room=None,
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=1,
                slot_codes=["S1"],
            ),
        )
        
        activity2 = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="CLASS",
            subject_or_activity="Subject B",
            section=None,
            room=None,
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=1,
                slot_codes=["S1"],  # Same slot as activity1
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity1, activity2],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        # Both activities should have overlap issues
        assert any(
            issue.code == "TEACHER_OVERLAP" and issue.severity == ValidationSeverity.ERROR
            for issue in activity1.issues
        )
        assert any(
            issue.code == "TEACHER_OVERLAP" and issue.severity == ValidationSeverity.ERROR
            for issue in activity2.issues
        )

    def test_multi_slot_activity_no_self_overlap(self, db: Session, program: dict):
        """Single LAB activity spanning S4+S5 should NOT overlap with itself."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="LAB",
            subject_or_activity="DBMS Lab",
            section=None,
            room=None,
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=4,
                slot_codes=["S4", "S5"],
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity],
        )
        
        validated = validate_canonical(timetable, db)
        
        # Should have NO overlap errors
        assert not any(
            issue.code == "TEACHER_OVERLAP"
            for issue in activity.issues
        )


class TestDuplicateDetection:
    """Test duplicate activity detection."""

    def test_duplicate_activity_detected(self, db: Session, program: dict):
        """Exact duplicate activities should produce ERROR."""
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        activity1 = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="CLASS",
            subject_or_activity="Data Structures",
            section="MCA-1A",
            room="CR-101",
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=1,
                slot_codes=["S1"],
            ),
        )
        
        activity2 = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="CLASS",
            subject_or_activity="Data Structures",  # Same
            section="MCA-1A",  # Same
            room="CR-101",
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=1,  # Same day
                slot_codes=["S1"],  # Same slot
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity1, activity2],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        assert any(
            issue.code == "SCHEDULE_DUPLICATE_ACTIVITY" and issue.severity == ValidationSeverity.ERROR
            for issue in activity2.issues
        )


class TestSourceLocationPreservation:
    """Test that source locations are preserved in validation issues."""

    def test_source_location_in_validation_issue(self, db: Session, program: dict):
        """Validation issues should include source locations."""
        source_loc = SourceLocation(
            source_type="XLSX",
            sheet_name="Schedule",
            row_number=15,
        )
        
        teacher = TeacherIdentity(
            acronym="TEST",
            name="Test Teacher",
            level="PG",
            program_name=program["name"],
            semester=1,
            department="CS",
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="CLASS",
            subject_or_activity="Test",
            section=None,
            room=None,
            notes=None,
            slot_range=ActivitySlotRange(
                day_of_week=7,  # Sunday - invalid
                slot_codes=["S1"],
                source_location=source_loc,
            ),
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
            activities=[activity],
        )
        
        validated = validate_canonical(timetable, db)
        
        # Find the Sunday error
        sunday_issues = [
            issue for issue in activity.issues
            if issue.code == "SCHEDULE_SUNDAY"
        ]
        
        assert len(sunday_issues) > 0
        assert len(sunday_issues[0].source_locations) > 0
        assert sunday_issues[0].source_locations[0].sheet_name == "Schedule"
        assert sunday_issues[0].source_locations[0].row_number == 15


class TestMultipleIssues:
    """Test that multiple validation issues can be returned."""

    def test_multiple_issues_returned(self, db: Session):
        """Timetable with multiple errors should return all of them."""
        teacher = TeacherIdentity(
            acronym="",  # Missing acronym
            name="",  # Missing name
            level="INVALID",  # Invalid level
            program_name="UNKNOWN",  # Unknown program
            semester=0,  # Invalid semester
            department="CS",
        )
        
        timetable = CanonicalTimetable(
            import_id="test",
            academic_year="2026-2027",
            teachers=[teacher],
        )
        
        validated = validate_canonical(timetable, db)
        
        assert validated.has_errors()
        # Should have multiple errors
        assert len(teacher.issues) >= 4  # Missing name, acronym, invalid level, invalid semester
