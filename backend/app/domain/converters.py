"""
Conversion adapters between transport models and canonical domain models.

This module provides bidirectional conversion between:
- ImportPreview (transport/UI representation) ↔ CanonicalTimetable (canonical domain)

The canonical model is the authoritative representation used by validation
and persistence. ImportPreview remains for API compatibility.
"""
from __future__ import annotations

from app.core.schedule_config import DAY_NAME_TO_ISO
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


def import_preview_to_canonical(preview: ImportPreview) -> CanonicalTimetable:
    """
    Convert ImportPreview (transport model) to CanonicalTimetable (canonical domain).
    
    This adapter preserves source location information where available and
    converts warnings/errors into structured ValidationIssue objects.
    """
    
    # Convert teachers
    teachers: list[TeacherIdentity] = []
    for t in preview.teachers:
        teacher = TeacherIdentity(
            acronym=t.acronym,
            name=t.name,
            level=t.level,
            program_name=t.program_name,
            semester=t.semester,
            department=t.department,
            resolved_teacher_id=t.resolved_teacher_id,
            action=t.action,
            issues=[
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="TEACHER_WARNING",
                    message=warning,
                    source_locations=[
                        SourceLocation(
                            source_type="XLSX",
                            sheet_name="Teachers",
                            display_context=t.row_ref,
                        )
                    ],
                    affected_entities={"teacher": t.acronym},
                )
                for warning in t.warnings
            ],
        )
        teachers.append(teacher)
    
    # Convert activities
    activities: list[ScheduleActivity] = []
    for day_name, rows in preview.days.items():
        day_iso = DAY_NAME_TO_ISO[day_name]
        for row in rows:
            # Extract source location from row_refs
            source_location = None
            if row.row_refs:
                # row_refs[0] format: "Schedule!3"
                ref = row.row_refs[0]
                if "!" in ref:
                    sheet, row_num_str = ref.split("!")
                    try:
                        row_number = int(row_num_str)
                        source_location = SourceLocation(
                            source_type="XLSX",
                            sheet_name=sheet,
                            row_number=row_number,
                        )
                    except ValueError:
                        source_location = SourceLocation(
                            source_type="XLSX",
                            display_context=ref,
                        )
            
            # Create slot range
            slot_range = ActivitySlotRange(
                day_of_week=day_iso,
                slot_codes=row.slot_ids,
                source_location=source_location,
            )
            
            # Create activity
            activity = ScheduleActivity(
                teacher_acronym=row.teacher_acronym,
                entry_type=row.entry_type,  # type: ignore[arg-type]
                subject_or_activity=row.subject_or_activity,
                section=row.section,
                room=row.room,
                notes=row.notes,
                slot_range=slot_range,
                issues=[
                    ValidationIssue(
                        severity=ValidationSeverity.WARNING,
                        code="SCHEDULE_WARNING",
                        message=warning,
                        source_locations=[source_location] if source_location else [],
                        affected_entities={
                            "teacher": row.teacher_acronym,
                            "day": day_name,
                            "slots": ",".join(row.slot_ids),
                        },
                    )
                    for warning in row.warnings
                ],
            )
            activities.append(activity)
    
    # Convert global issues
    global_issues: list[ValidationIssue] = []
    for warning in preview.warnings:
        global_issues.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="GLOBAL_WARNING",
                message=warning,
            )
        )
    for error in preview.errors:
        global_issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="GLOBAL_ERROR",
                message=error,
            )
        )
    
    return CanonicalTimetable(
        import_id=preview.import_id,
        academic_year=preview.academic_year,
        teachers=teachers,
        activities=activities,
        global_issues=global_issues,
    )


def canonical_to_import_preview(canonical: CanonicalTimetable) -> ImportPreview:
    """
    Convert CanonicalTimetable (canonical domain) to ImportPreview (transport model).
    
    This adapter reconstructs the ImportPreview format for API compatibility
    while deriving data from the canonical representation.
    """
    
    # Convert teachers
    teachers: list[TeacherImportRow] = []
    for t in canonical.teachers:
        warnings = [
            issue.message
            for issue in t.issues
            if issue.severity == ValidationSeverity.WARNING
        ]
        
        # Reconstruct row_ref from source location if available
        row_ref = f"Teachers!?"
        if t.issues:
            for issue in t.issues:
                if issue.source_locations:
                    loc = issue.source_locations[0]
                    if loc.display_context:
                        row_ref = loc.display_context
                        break
        
        teachers.append(
            TeacherImportRow(
                row_ref=row_ref,
                action=t.action,
                name=t.name,
                acronym=t.acronym,
                level=t.level,
                program_name=t.program_name,
                semester=t.semester,
                department=t.department,
                resolved_teacher_id=t.resolved_teacher_id,
                warnings=warnings,
            )
        )
    
    # Convert activities grouped by day
    from app.core.schedule_config import ISO_TO_DAY_NAME
    
    days: dict[str, list[ScheduleImportRow]] = {}
    for activity in canonical.activities:
        day_name = ISO_TO_DAY_NAME[activity.slot_range.day_of_week]
        
        warnings = [
            issue.message
            for issue in activity.issues
            if issue.severity == ValidationSeverity.WARNING
        ]
        
        # Reconstruct row_refs from source location
        row_refs = []
        if activity.slot_range.source_location:
            loc = activity.slot_range.source_location
            if loc.display_context:
                row_refs.append(loc.display_context)
            elif loc.sheet_name and loc.row_number:
                row_refs.append(f"{loc.sheet_name}!{loc.row_number}")
        if not row_refs:
            row_refs.append("Schedule!?")
        
        schedule_row = ScheduleImportRow(
            row_refs=row_refs,
            teacher_acronym=activity.teacher_acronym,
            day=day_name,
            slot_ids=activity.slot_range.slot_codes,
            entry_type=activity.entry_type,
            subject_or_activity=activity.subject_or_activity,
            section=activity.section,
            room=activity.room,
            notes=activity.notes,
            warnings=warnings,
        )
        
        days.setdefault(day_name, []).append(schedule_row)
    
    # Convert global issues
    warnings = [
        issue.message
        for issue in canonical.global_issues
        if issue.severity == ValidationSeverity.WARNING
    ]
    errors = [
        issue.message
        for issue in canonical.global_issues
        if issue.severity == ValidationSeverity.ERROR
    ]
    
    # Collect ERROR-level issues from teachers and activities
    for teacher in canonical.teachers:
        for issue in teacher.issues:
            if issue.severity == ValidationSeverity.ERROR:
                errors.append(issue.message)
    
    for activity in canonical.activities:
        for issue in activity.issues:
            if issue.severity == ValidationSeverity.ERROR:
                errors.append(issue.message)
    
    return ImportPreview(
        import_id=canonical.import_id,
        academic_year=canonical.academic_year,
        teachers=teachers,
        days=days,
        warnings=warnings,
        errors=errors,
    )
