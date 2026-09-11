"""
Canonical domain validation engine.

This module provides source-agnostic validation for CanonicalTimetable objects.
It operates independently of transport formats (XLSX, DOCX, etc.) and enforces
institutional business rules.

The ValidationEngine:
- Validates teacher identities
- Validates schedule activities
- Detects overlaps and conflicts
- Populates ValidationIssue objects with structured feedback
- Does NOT modify the canonical timetable structure
- Returns the same CanonicalTimetable with issues populated

DB-dependent validation (program resolution, teacher DB conflicts) is performed
separately by the database validator.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.schedule_config import SLOT_CODES, VALID_DAY_NAMES, VALID_LAB_PAIRS, WORKING_DAYS, is_valid_academic_year
from app.domain.timetable import (
    CanonicalTimetable,
    ScheduleActivity,
    SourceLocation,
    TeacherIdentity,
    ValidationIssue,
    ValidationSeverity,
)
from app.models.models import Program, Teacher

# Valid teacher levels
VALID_LEVELS: frozenset[str] = frozenset({"UG", "PG"})

# Valid entry types
VALID_ENTRY_TYPES: frozenset[str] = frozenset({"CLASS", "LAB", "OTHER"})

# Break/lunch slots (not schedulable)
BREAK_SLOTS: frozenset[str] = frozenset()  # Currently none defined as codes


def validate_canonical(canonical: CanonicalTimetable, db: Session) -> CanonicalTimetable:
    """
    Validate a CanonicalTimetable against domain rules and DB state.
    
    Returns the same canonical object with ValidationIssue objects populated in:
    - canonical.global_issues
    - canonical.teachers[].issues
    - canonical.activities[].issues
    
    This function performs:
    1. Academic year validation
    2. Teacher validation (name, acronym, level, semester, duplicates)
    3. DB-level teacher resolution (program lookup, existing teacher conflicts)
    4. Schedule validation (day, slots, duration, LAB rules)
    5. Overlap detection (same teacher, same day, same slot)
    6. Duplicate activity detection
    
    Does NOT modify the structure of the canonical timetable.
    
    NOTE: Does NOT clear pre-existing issues from normalization/parsing.
    This allows parse/normalization errors to flow through validation.
    """
    # Do NOT clear existing issues - they may come from parser/normalizer
    # We only ADD new validation issues
    
    # Run validation steps
    _validate_academic_year(canonical)
    _validate_teachers(canonical)
    _validate_teacher_db_resolution(canonical, db)
    _validate_activities(canonical)
    _validate_overlaps(canonical)
    _validate_duplicates(canonical)
    _validate_teacher_activity_references(canonical)
    
    return canonical


# ---------------------------------------------------------------------------
# Academic Year Validation
# ---------------------------------------------------------------------------


def _validate_academic_year(canonical: CanonicalTimetable) -> None:
    """Validate academic year format."""
    if not is_valid_academic_year(canonical.academic_year):
        canonical.global_issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="INVALID_ACADEMIC_YEAR",
                message=(
                    f"Academic year '{canonical.academic_year}' is invalid. "
                    "Expected YYYY-YYYY where the second year equals first + 1 (e.g. 2025-2026)."
                ),
                affected_entities={"academic_year": canonical.academic_year},
                suggestion="Use format YYYY-YYYY with consecutive years",
            )
        )


# ---------------------------------------------------------------------------
# Teacher Validation
# ---------------------------------------------------------------------------


def _validate_teachers(canonical: CanonicalTimetable) -> None:
    """
    Validate teacher identities.
    
    Rules:
    - Name required
    - Acronym required
    - Level must be UG or PG
    - Semester must be positive integer
    - No duplicate teacher identities within the timetable
    """
    seen_acronyms: dict[str, TeacherIdentity] = {}
    
    for teacher in canonical.teachers:
        # Missing name
        if not teacher.name or not teacher.name.strip():
            teacher.issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="TEACHER_MISSING_NAME",
                    message=f"Teacher acronym '{teacher.acronym}': name is required",
                    affected_entities={"teacher": teacher.acronym},
                    suggestion="Provide a valid teacher name",
                )
            )
        
        # Missing acronym
        if not teacher.acronym or not teacher.acronym.strip():
            teacher.issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="TEACHER_MISSING_ACRONYM",
                    message="Teacher acronym is required",
                    affected_entities={"teacher": teacher.name or "(unnamed)"},
                    suggestion="Provide a valid teacher acronym",
                )
            )
        
        # Invalid level
        if teacher.level not in VALID_LEVELS:
            teacher.issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="TEACHER_INVALID_LEVEL",
                    message=f"Teacher '{teacher.acronym}': invalid level '{teacher.level}'. Expected 'UG' or 'PG'",
                    affected_entities={"teacher": teacher.acronym, "level": teacher.level},
                    suggestion="Set level to 'UG' or 'PG'",
                )
            )
        
        # Invalid semester
        if teacher.semester <= 0:
            teacher.issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="TEACHER_INVALID_SEMESTER",
                    message=f"Teacher '{teacher.acronym}': semester must be a positive integer, got {teacher.semester}",
                    affected_entities={"teacher": teacher.acronym, "semester": str(teacher.semester)},
                    suggestion="Provide a valid positive semester number",
                )
            )
        
        # Duplicate acronym within the timetable
        acronym_key = teacher.acronym.strip().upper()
        if acronym_key in seen_acronyms:
            first_teacher = seen_acronyms[acronym_key]
            teacher.issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="TEACHER_DUPLICATE_IDENTITY",
                    message=f"Duplicate teacher acronym '{teacher.acronym}' within this import",
                    affected_entities={
                        "teacher": teacher.acronym,
                        "first_name": first_teacher.name,
                        "duplicate_name": teacher.name,
                    },
                    suggestion="Each teacher acronym must be unique within the import",
                )
            )
        else:
            seen_acronyms[acronym_key] = teacher


def _validate_teacher_db_resolution(canonical: CanonicalTimetable, db: Session) -> None:
    """
    Validate teachers against the database.
    
    Rules:
    - Program must exist in DB
    - Check for existing teacher conflicts (same acronym, different identity)
    - Set teacher action (CREATE/REUSE/CONFLICT)
    """
    for teacher in canonical.teachers:
        # Skip if already has errors (don't cascade)
        if any(issue.severity == ValidationSeverity.ERROR for issue in teacher.issues):
            continue
        
        # Resolve program
        program = (
            db.query(Program)
            .filter(
                func.lower(Program.name) == teacher.program_name.lower(),
                Program.level == teacher.level,
                Program.is_active.is_(True),
            )
            .first()
        )
        
        if program is None:
            # Try to find the program with a different level for better error message
            any_level_prog = (
                db.query(Program)
                .filter(func.lower(Program.name) == teacher.program_name.lower())
                .first()
            )
            if any_level_prog:
                teacher.issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="TEACHER_UNKNOWN_PROGRAM",
                        message=(
                            f"Teacher '{teacher.acronym}': Program '{teacher.program_name}' "
                            f"is registered at level '{any_level_prog.level}', "
                            f"but teacher specifies level '{teacher.level}'"
                        ),
                        affected_entities={
                            "teacher": teacher.acronym,
                            "program": teacher.program_name,
                            "requested_level": teacher.level,
                            "available_level": any_level_prog.level,
                        },
                        suggestion="Correct the level or program name",
                    )
                )
            else:
                teacher.issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="TEACHER_UNKNOWN_PROGRAM",
                        message=(
                            f"Teacher '{teacher.acronym}': Unknown program "
                            f"'{teacher.program_name}' at level '{teacher.level}'"
                        ),
                        affected_entities={
                            "teacher": teacher.acronym,
                            "program": teacher.program_name,
                            "level": teacher.level,
                        },
                        suggestion="Ensure the program exists in the database",
                    )
                )
            continue
        
        # Check for existing teacher with same acronym
        existing: Teacher | None = (
            db.query(Teacher)
            .filter(func.lower(Teacher.acronym) == teacher.acronym.lower())
            .first()
        )
        
        if existing is not None:
            teacher.resolved_teacher_id = existing.id
            
            # Check for identity conflicts
            conflicts: list[str] = []
            if existing.name.strip().lower() != teacher.name.strip().lower():
                conflicts.append(f"name (DB: '{existing.name}', import: '{teacher.name}')")
            if existing.level != teacher.level:
                conflicts.append(f"level (DB: '{existing.level}', import: '{teacher.level}')")
            if existing.program_id != program.id:
                conflicts.append(f"program (DB program_id: {existing.program_id}, import program_id: {program.id})")
            
            if conflicts:
                teacher.action = "CONFLICT"
                teacher.issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="TEACHER_CONFLICT",
                        message=(
                            f"Teacher '{teacher.acronym}' exists in DB with conflicting "
                            f"identity: {'; '.join(conflicts)}"
                        ),
                        affected_entities={"teacher": teacher.acronym},
                        suggestion="Correct the import data to match existing teacher or use a different acronym",
                    )
                )
            else:
                teacher.action = "REUSE"
        else:
            teacher.action = "CREATE"


def _validate_teacher_activity_references(canonical: CanonicalTimetable) -> None:
    """
    Check that activities reference valid teachers and flag teachers with no activities.
    """
    teacher_acronyms = {t.acronym for t in canonical.teachers}
    teachers_with_activities = set()
    
    for activity in canonical.activities:
        teachers_with_activities.add(activity.teacher_acronym)
        
        # Check if teacher exists
        if activity.teacher_acronym not in teacher_acronyms:
            activity.issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="ACTIVITY_UNKNOWN_TEACHER",
                    message=f"Activity references unknown teacher acronym '{activity.teacher_acronym}'",
                    source_locations=[activity.slot_range.source_location] if activity.slot_range.source_location else [],
                    affected_entities={"teacher": activity.teacher_acronym},
                    suggestion="Ensure the teacher is defined in the import",
                )
            )
    
    # Flag teachers with no activities (INFO level)
    for teacher in canonical.teachers:
        if teacher.acronym not in teachers_with_activities:
            teacher.issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.INFO,
                    code="TEACHER_NO_ACTIVITIES",
                    message=f"Teacher '{teacher.acronym}' has no schedule activities",
                    affected_entities={"teacher": teacher.acronym},
                )
            )


# ---------------------------------------------------------------------------
# Schedule Activity Validation
# ---------------------------------------------------------------------------


def _validate_activities(canonical: CanonicalTimetable) -> None:
    """
    Validate schedule activities.
    
    Rules:
    - Valid day (Monday-Saturday, no Sunday)
    - Valid slot codes
    - No break/lunch slots
    - LAB: exactly 2 consecutive working slots
    - CLASS/OTHER: 1-2 consecutive working slots
    - Max 2 slots for any activity
    """
    for activity in canonical.activities:
        _validate_activity_day(activity)
        _validate_activity_slots(activity)
        _validate_activity_duration(activity)


def _validate_activity_day(activity: ScheduleActivity) -> None:
    """Validate activity day of week."""
    # Check for Sunday (day_of_week == 7)
    if activity.slot_range.day_of_week == 7:
        activity.issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="SCHEDULE_SUNDAY",
                message=f"Sunday is not a working day for teacher '{activity.teacher_acronym}'",
                source_locations=[activity.slot_range.source_location] if activity.slot_range.source_location else [],
                affected_entities={
                    "teacher": activity.teacher_acronym,
                    "day": "sunday",
                },
                suggestion="Schedule activities only on Monday through Saturday",
            )
        )
    
    # Check for invalid day (not 1-7)
    if not (1 <= activity.slot_range.day_of_week <= 7):
        activity.issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="SCHEDULE_INVALID_DAY",
                message=f"Invalid day of week: {activity.slot_range.day_of_week} (must be 1-7, where 1=Monday, 7=Sunday)",
                source_locations=[activity.slot_range.source_location] if activity.slot_range.source_location else [],
                affected_entities={
                    "teacher": activity.teacher_acronym,
                    "day_of_week": str(activity.slot_range.day_of_week),
                },
                suggestion="Use valid ISO day of week (1=Monday through 6=Saturday)",
            )
        )


def _validate_activity_slots(activity: ScheduleActivity) -> None:
    """Validate activity slot codes."""
    for slot_code in activity.slot_range.slot_codes:
        # Check for invalid slot code
        if slot_code not in SLOT_CODES:
            activity.issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="SCHEDULE_INVALID_SLOT",
                    message=f"Invalid slot code '{slot_code}' for teacher '{activity.teacher_acronym}'",
                    source_locations=[activity.slot_range.source_location] if activity.slot_range.source_location else [],
                    affected_entities={
                        "teacher": activity.teacher_acronym,
                        "slot": slot_code,
                    },
                    suggestion=f"Valid slot codes are: {', '.join(SLOT_CODES)}",
                )
            )


def _validate_activity_duration(activity: ScheduleActivity) -> None:
    """Validate activity duration rules."""
    slot_count = len(activity.slot_range.slot_codes)
    
    # 3+ slots invalid
    if slot_count > 2:
        activity.issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="SCHEDULE_INVALID_DURATION",
                message=f"Activity spans {slot_count} slots, maximum is 2 consecutive slots",
                source_locations=[activity.slot_range.source_location] if activity.slot_range.source_location else [],
                affected_entities={
                    "teacher": activity.teacher_acronym,
                    "slots": ",".join(activity.slot_range.slot_codes),
                    "slot_count": str(slot_count),
                },
                suggestion="Split into multiple activities or reduce to 1-2 consecutive slots",
            )
        )
        return  # Don't check further rules if already too many slots
    
    # LAB specific rules
    if activity.entry_type == "LAB":
        if slot_count != 2:
            activity.issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="SCHEDULE_LAB_INVALID_DURATION",
                    message=f"LAB activity must span exactly 2 slots, got {slot_count}",
                    source_locations=[activity.slot_range.source_location] if activity.slot_range.source_location else [],
                    affected_entities={
                        "teacher": activity.teacher_acronym,
                        "entry_type": "LAB",
                        "slots": ",".join(activity.slot_range.slot_codes),
                        "slot_count": str(slot_count),
                    },
                    suggestion="LAB activities must occupy exactly 2 consecutive working slots",
                )
            )
        elif slot_count == 2:
            # Check if slots are consecutive
            slot_pair = frozenset(activity.slot_range.slot_codes)
            if slot_pair not in VALID_LAB_PAIRS:
                activity.issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="SCHEDULE_LAB_NON_CONSECUTIVE",
                        message=(
                            f"LAB slots {activity.slot_range.slot_codes} are not consecutive "
                            f"or are separated by a break"
                        ),
                        source_locations=[activity.slot_range.source_location] if activity.slot_range.source_location else [],
                        affected_entities={
                            "teacher": activity.teacher_acronym,
                            "entry_type": "LAB",
                            "slots": ",".join(activity.slot_range.slot_codes),
                        },
                        suggestion="LAB must use consecutive slots with no break between them (e.g., S1+S2, S4+S5, not S3+S4)",
                    )
                )
    
    # CLASS/OTHER: 1-2 consecutive slots
    if activity.entry_type in ("CLASS", "OTHER") and slot_count == 2:
        # Check if slots are consecutive (in SLOT_CODES order)
        try:
            idx1 = SLOT_CODES.index(activity.slot_range.slot_codes[0])
            idx2 = SLOT_CODES.index(activity.slot_range.slot_codes[1])
            if abs(idx1 - idx2) != 1:
                activity.issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="SCHEDULE_NON_CONSECUTIVE",
                        message=f"{activity.entry_type} activity slots must be consecutive",
                        source_locations=[activity.slot_range.source_location] if activity.slot_range.source_location else [],
                        affected_entities={
                            "teacher": activity.teacher_acronym,
                            "entry_type": activity.entry_type,
                            "slots": ",".join(activity.slot_range.slot_codes),
                        },
                        suggestion="Use consecutive slots (e.g., S1+S2, S6+S7)",
                    )
                )
        except ValueError:
            # Slot not in SLOT_CODES - already caught by _validate_activity_slots
            pass


# ---------------------------------------------------------------------------
# Overlap Detection
# ---------------------------------------------------------------------------


def _validate_overlaps(canonical: CanonicalTimetable) -> None:
    """
    Detect teacher overlaps: same teacher, same day, same slot, different activities.
    
    A single logical activity spanning multiple slots does NOT overlap with itself.
    """
    # Build occupancy map: (teacher, day, slot) -> [activities]
    occupancy: dict[tuple[str, int, str], list[ScheduleActivity]] = defaultdict(list)
    
    for activity in canonical.activities:
        teacher = activity.teacher_acronym
        day = activity.slot_range.day_of_week
        
        for slot in activity.slot_range.slot_codes:
            key = (teacher, day, slot)
            occupancy[key].append(activity)
    
    # Check for overlaps
    reported_overlaps: set[tuple[str, int, str]] = set()
    
    for (teacher, day, slot), activities in occupancy.items():
        if len(activities) > 1:
            # Multiple activities occupy the same slot
            key = (teacher, day, slot)
            if key not in reported_overlaps:
                reported_overlaps.add(key)
                
                # Get day name
                from app.core.schedule_config import ISO_TO_DAY_NAME
                day_name = ISO_TO_DAY_NAME.get(day, f"day-{day}")
                
                # Create overlap issue for each activity
                activity_descriptions = []
                for act in activities:
                    desc = f"{act.entry_type}"
                    if act.subject_or_activity:
                        desc += f": {act.subject_or_activity}"
                    if act.section:
                        desc += f" ({act.section})"
                    activity_descriptions.append(desc)
                
                for activity in activities:
                    activity.issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            code="TEACHER_OVERLAP",
                            message=(
                                f"Teacher '{teacher}' has overlapping activities on "
                                f"{day_name} at slot {slot}: {'; '.join(activity_descriptions)}"
                            ),
                            source_locations=[activity.slot_range.source_location] if activity.slot_range.source_location else [],
                            affected_entities={
                                "teacher": teacher,
                                "day": day_name,
                                "slot": slot,
                                "activity_count": str(len(activities)),
                            },
                            suggestion="Remove or reschedule one of the conflicting activities",
                        )
                    )


# ---------------------------------------------------------------------------
# Duplicate Activity Detection
# ---------------------------------------------------------------------------


def _validate_duplicates(canonical: CanonicalTimetable) -> None:
    """
    Detect duplicate activities.
    
    A duplicate is defined as two activities with:
    - Same teacher
    - Same day
    - Same slot range (exact match)
    - Same entry type
    - Same subject/activity (if both specified)
    """
    # Build activity signature map
    seen: dict[tuple, ScheduleActivity] = {}
    
    for activity in canonical.activities:
        # Create a signature for this activity
        signature = (
            activity.teacher_acronym,
            activity.slot_range.day_of_week,
            tuple(sorted(activity.slot_range.slot_codes)),  # Sort to handle order variations
            activity.entry_type,
            (activity.subject_or_activity or "").strip().lower(),
        )
        
        if signature in seen:
            first_activity = seen[signature]
            activity.issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="SCHEDULE_DUPLICATE_ACTIVITY",
                    message=(
                        f"Duplicate activity for teacher '{activity.teacher_acronym}': "
                        f"{activity.entry_type} on same day/slots"
                    ),
                    source_locations=[
                        loc for loc in [
                            first_activity.slot_range.source_location,
                            activity.slot_range.source_location
                        ] if loc is not None
                    ],
                    affected_entities={
                        "teacher": activity.teacher_acronym,
                        "entry_type": activity.entry_type,
                        "subject": activity.subject_or_activity or "(none)",
                    },
                    suggestion="Remove the duplicate activity entry",
                )
            )
        else:
            seen[signature] = activity
