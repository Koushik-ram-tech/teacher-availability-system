"""Convert DOCXImportPreview to CanonicalTimetable.

This module bridges the staging/transport layer to the domain layer.
"""

from typing import Optional
import re

from app.core.schedule_config import DAY_NAME_TO_ISO
from app.domain.timetable import (
    ActivitySlotRange,
    CanonicalTimetable,
    ScheduleActivity,
    TeacherIdentity,
    SourceLocation,
)
from .staging import DOCXImportPreview


def convert_preview_to_canonical(
    preview: DOCXImportPreview,
    program_name: str = "MCA",
    level: str = "PG",
    semester: int = 1
) -> Optional[CanonicalTimetable]:
    """Convert ImportPreview to CanonicalTimetable.

    ONLY resolved blocks convert.
    Unresolved blocks MUST be empty or this fails.

    After conversion, staging data is discarded.

    Args:
        preview: DOCXImportPreview with resolved blocks
        program_name: Program name (default: "MCA")
        level: Level (default: "PG")
        semester: Semester number (default: 1, infer from academic_year if needed)

    Returns:
        CanonicalTimetable or None if errors/unresolved blocks exist
    """
    # Check for errors
    if preview.errors:
        return None

    # Check for unresolved blocks (cannot publish with unresolved)
    if preview.occupancy_review_blocks:
        return None

    # Extract unique teachers
    teachers = extract_teacher_identities(preview, program_name, level, semester)

    # Convert occupancy_ready blocks to ScheduleActivity
    activities = []
    for block in preview.occupancy_ready_blocks:
        # Convert day name to ISO
        day_iso = DAY_NAME_TO_ISO.get(block.day, 1)

        # Create slot range
        slot_range = ActivitySlotRange(
            day_of_week=day_iso,
            slot_codes=block.slots,
            source_location=block.source_location
        )

        combined_activity = ", ".join(ac.code for ac in block.activity_candidates) or "Unknown Activity"
        entry_type = block.activity_candidates[0].inferred_type if block.activity_candidates else "CLASS"
        combined_resource = ", ".join(rc.code for rc in block.resource_candidates) if block.resource_candidates else None

        for teacher in block.teacher_candidates:
            activities.append(ScheduleActivity(
                teacher_acronym=teacher.normalized_acronym,
                entry_type=entry_type,  # type: ignore[arg-type]
                subject_or_activity=combined_activity,
                section=block.section,
                room=combined_resource,
                notes=None,
                slot_range=slot_range,
                issues=[]
            ))

    # Create CanonicalTimetable
    return CanonicalTimetable(
        import_id=preview.import_session_id,
        academic_year=preview.academic_year,
        teachers=teachers,
        activities=activities,
        global_issues=[]
    )


def extract_teacher_identities(
    preview: DOCXImportPreview,
    program_name: str,
    level: str,
    semester: int
) -> list[TeacherIdentity]:
    """Extract unique teacher identities from resolved blocks.

    Args:
        preview: DOCXImportPreview
        program_name: Program name (e.g., "MCA")
        level: Level (e.g., "PG")
        semester: Semester number

    Returns:
        List of TeacherIdentity objects
    """
    teachers_map = {}

    for block in preview.occupancy_ready_blocks:
        for teacher in block.teacher_candidates:
            acronym = teacher.normalized_acronym
            if acronym not in teachers_map:
                # Get full name from legend if available
                full_name = preview.faculty_legend.get(acronym, "")

                teachers_map[acronym] = TeacherIdentity(
                    acronym=acronym,
                    name=full_name if full_name else acronym,
                    level=level,
                    program_name=program_name,
                    semester=semester,
                    department=preview.department,
                    resolved_teacher_id=None,
                    action="CREATE"
                )

    return list(teachers_map.values())


def infer_semester_from_academic_year(academic_year: str) -> int:
    """Infer semester number from academic year string.

    Args:
        academic_year: e.g., "2026-2027", "2026-Even"

    Returns:
        Semester number (1 for Odd, 2 for Even, default 1)
    """
    if "odd" in academic_year.lower():
        return 1
    elif "even" in academic_year.lower():
        return 2
    return 1
