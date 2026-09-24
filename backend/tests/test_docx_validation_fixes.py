from sqlalchemy.orm import Session
from app.domain.timetable import CanonicalTimetable, TeacherIdentity, ScheduleActivity, ActivitySlotRange
from app.domain.validation import validate_canonical
from app.models.models import Program

def test_vmerge_duplicates_ignored(db: Session, program: dict):
    """Test that two identical activities with the same source_cell_text and group_index don't conflict."""
    teacher = TeacherIdentity(acronym="TS", name="Smt. T Sunitha", level=program["level"], program_name=program["name"], semester=1, department="CS")
    
    act1 = ScheduleActivity(
        teacher_acronym="TS", entry_type="CLASS", subject_or_activity="DBMS", section="A", room="R1", notes=None,
        slot_range=ActivitySlotRange(day_of_week=1, slot_codes=["S1", "S2"]),
        group_index=0, source_cell_text="DBMS / TS"
    )
    act2 = ScheduleActivity(
        teacher_acronym="TS", entry_type="CLASS", subject_or_activity="DBMS", section="B", room="R1", notes=None,
        slot_range=ActivitySlotRange(day_of_week=1, slot_codes=["S1", "S2"]),
        group_index=0, source_cell_text="DBMS / TS"
    )
    
    timetable = CanonicalTimetable(import_id="test", academic_year="2026-2027", teachers=[teacher], activities=[act1, act2])
    validated = validate_canonical(timetable, db)
    assert not validated.has_errors(), f"vMerge duplicate produced errors: {[issue.message for act in validated.activities for issue in act.issues]}"

def test_empty_teacher_acronym_ignored(db: Session, program: dict):
    """Test that empty teacher acronyms are ignored during overlap and reference validation."""
    teacher = TeacherIdentity(acronym="TS", name="Smt. T Sunitha", level=program["level"], program_name=program["name"], semester=1, department="CS")
    
    act1 = ScheduleActivity(
        teacher_acronym="", entry_type="OTHER", subject_or_activity="Placement", section="A", room="R1", notes=None,
        slot_range=ActivitySlotRange(day_of_week=1, slot_codes=["S1", "S2"])
    )
    act2 = ScheduleActivity(
        teacher_acronym="", entry_type="OTHER", subject_or_activity="Placement", section="B", room="R1", notes=None,
        slot_range=ActivitySlotRange(day_of_week=1, slot_codes=["S1", "S2"])
    )
    
    timetable = CanonicalTimetable(import_id="test", academic_year="2026-2027", teachers=[teacher], activities=[act1, act2])
    validated = validate_canonical(timetable, db)
    
    errors = [issue.message for act in validated.activities for issue in act.issues if issue.severity.value == "ERROR"]
    assert len(errors) == 0, f"Empty teacher produced errors: {errors}"

def test_student_managed_duration_exception(db: Session, program: dict):
    """Test that student-managed activities can span 3+ slots."""
    teacher = TeacherIdentity(acronym="TS", name="Smt. T Sunitha", level=program["level"], program_name=program["name"], semester=1, department="CS")
    
    act1 = ScheduleActivity(
        teacher_acronym="", entry_type="OTHER", subject_or_activity="Placement", section="A", room="R1", notes=None,
        slot_range=ActivitySlotRange(day_of_week=1, slot_codes=["S6", "S7", "S8"])
    )
    
    timetable = CanonicalTimetable(import_id="test", academic_year="2026-2027", teachers=[teacher], activities=[act1])
    validated = validate_canonical(timetable, db)
    
    errors = [issue.message for act in validated.activities for issue in act.issues if issue.severity.value == "ERROR"]
    assert len(errors) == 0, f"Student-managed duration exception failed: {errors}"
