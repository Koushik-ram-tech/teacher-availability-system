"""
Conversion adapters between DOCX import models and canonical domain models.

This module provides conversion between:
- DOCXImportPreview (DOCX-specific transport) → CanonicalTimetable (canonical domain)
- CanonicalTimetable → DOCXImportPreview (for API responses)

IMPORTANT: DOCXImportPreview can only convert to CanonicalTimetable when all
required blocks are resolved. Unresolved blocks prevent canonical conversion.
"""
from __future__ import annotations

import uuid

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
from app.schemas.docx_imports import (
    DOCXImportPreview,
    DOCXParserIssue,
    ResolvedActivity,
)


def docx_preview_to_canonical(preview: DOCXImportPreview) -> CanonicalTimetable:
    """
    Convert DOCXImportPreview to CanonicalTimetable.

    PRECONDITION: preview.can_convert_to_canonical() must be True.
    Raises ValueError if unresolved blocks remain.

    This delegates to the converter module which already implements
    the conversion logic from the parser's internal structures.
    """
    if not preview.can_convert_to_canonical():
        unresolved_count = sum(1 for b in preview.unresolved_blocks if b.resolution_required)
        raise ValueError(
            f"Cannot convert to canonical: {unresolved_count} required unresolved blocks remain. "
            "Apply manual resolution or exclude blocks before conversion."
        )

    # Convert resolved activities to canonical format
    activities: list[ScheduleActivity] = []
    teacher_acronyms = set()

    for resolved in preview.resolved_activities:
        teacher_acronyms.add(resolved.teacher_acronym)

        # Parse source location
        source_location = SourceLocation(
            source_type="DOCX",
            display_context=resolved.source_location,
        )

        # Create slot range
        day_iso = DAY_NAME_TO_ISO.get(resolved.day.lower())
        if day_iso is None:
            raise ValueError(f"Invalid day name: {resolved.day}")

        slot_range = ActivitySlotRange(
            day_of_week=day_iso,
            slot_codes=resolved.slots,
            source_location=source_location,
        )

        # Create activity
        activity = ScheduleActivity(
            teacher_acronym=resolved.teacher_acronym,
            entry_type=resolved.entry_type,  # type: ignore[arg-type]
            subject_or_activity=resolved.subject_or_activity,
            section=resolved.section,
            room=resolved.resource_code,  # Provenance text (comma-joined), legacy compat
            resource_codes=list(resolved.resource_codes),  # Individual codes (authoritative)
            notes=resolved.notes,  # Carries "External: Ind*" when applicable
            slot_range=slot_range,
        )
        activities.append(activity)

    # Create teacher identities from faculty legend + activities
    teachers: list[TeacherIdentity] = []
    faculty_legend_map = {entry.acronym: entry.full_name for entry in preview.faculty_legend}

    # Infer semester from academic_year (e.g., "2026-Odd" → semester 1)
    semester = 1
    if "even" in preview.academic_year.lower():
        semester = 2

    # For DOCX, program_name must match database.
    # ValidationEngine will look up the program by name.
    # The department "Computer Applications" maps to program "Master of Computer Applications"
    program_name = "Master of Computer Applications" if "computer" in preview.department.lower() else preview.department

    for acronym in sorted(teacher_acronyms):
        # Skip empty acronyms (resource-only / external-only blocks)
        if not acronym:
            continue
        teacher = TeacherIdentity(
            acronym=acronym,
            name=faculty_legend_map.get(acronym, acronym),
            level="PG",  # Default for MCA department
            program_name=program_name,
            semester=semester,
            department=preview.department,
            resolved_teacher_id=None,  # Not yet resolved
            action="CREATE",  # Default to CREATE, validation will update if teacher exists
        )
        teachers.append(teacher)

    # Convert parser issues to global issues
    global_issues: list[ValidationIssue] = []

    for error in preview.errors:
        source_locations = []
        if error.source_location:
            source_locations.append(
                SourceLocation(
                    source_type="DOCX",
                    display_context=error.source_location,
                )
            )

        global_issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code=error.code,
                message=error.message,
                source_locations=source_locations,
                affected_entities=error.affected_entities,
            )
        )

    for warning in preview.warnings:
        source_locations = []
        if warning.source_location:
            source_locations.append(
                SourceLocation(
                    source_type="DOCX",
                    display_context=warning.source_location,
                )
            )

        global_issues.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code=warning.code,
                message=warning.message,
                source_locations=source_locations,
                affected_entities=warning.affected_entities,
            )
        )

    return CanonicalTimetable(
        import_id=preview.import_id,
        academic_year=preview.academic_year,
        teachers=teachers,
        activities=activities,
        global_issues=global_issues,
    )


def canonical_to_docx_preview(
    canonical: CanonicalTimetable,
    original_preview: DOCXImportPreview,
) -> DOCXImportPreview:
    """
    Convert CanonicalTimetable back to DOCXImportPreview.

    This is used after validation to return an updated preview with
    validation issues attached.

    IMPORTANT: This preserves the original unresolved blocks since
    CanonicalTimetable doesn't contain that information.
    """
    from app.core.schedule_config import ISO_TO_DAY_NAME

    # Convert activities back to resolved activities
    resolved_activities: list[ResolvedActivity] = []

    for activity in canonical.activities:
        day_name = ISO_TO_DAY_NAME[activity.slot_range.day_of_week]

        source_location = "DOCX"
        if activity.slot_range.source_location:
            source_location = activity.slot_range.source_location.to_display()

        resolved = ResolvedActivity(
            day=day_name,
            section=activity.section or "",
            slots=activity.slot_range.slot_codes,
            teacher_acronym=activity.teacher_acronym,
            subject_or_activity=activity.subject_or_activity or "",
            resource_code=activity.room,  # Map from legacy room field
            source_location=source_location,
            entry_type=activity.entry_type,
            is_multi_slot=len(activity.slot_range.slot_codes) > 1,
            is_manually_resolved=False,  # TODO: track this properly
        )
        resolved_activities.append(resolved)

    # Convert global issues back to parser issues
    errors: list[DOCXParserIssue] = []
    warnings: list[DOCXParserIssue] = []

    for issue in canonical.global_issues:
        source_location = None
        if issue.source_locations:
            source_location = issue.source_locations[0].to_display()

        parser_issue = DOCXParserIssue(
            severity=issue.severity.value,
            code=issue.code,
            message=issue.message,
            source_location=source_location,
            affected_entities=issue.affected_entities,
        )

        if issue.severity == ValidationSeverity.ERROR:
            errors.append(parser_issue)
        elif issue.severity == ValidationSeverity.WARNING:
            warnings.append(parser_issue)

    # Collect issues from teachers and activities
    for teacher in canonical.teachers:
        for issue in teacher.issues:
            source_location = None
            if issue.source_locations:
                source_location = issue.source_locations[0].to_display()

            parser_issue = DOCXParserIssue(
                severity=issue.severity.value,
                code=issue.code,
                message=issue.message,
                source_location=source_location,
                affected_entities=issue.affected_entities,
            )

            if issue.severity == ValidationSeverity.ERROR:
                errors.append(parser_issue)
            elif issue.severity == ValidationSeverity.WARNING:
                warnings.append(parser_issue)

    for activity in canonical.activities:
        for issue in activity.issues:
            source_location = None
            if activity.slot_range.source_location:
                source_location = activity.slot_range.source_location.to_display()

            parser_issue = DOCXParserIssue(
                severity=issue.severity.value,
                code=issue.code,
                message=issue.message,
                source_location=source_location,
                affected_entities=issue.affected_entities,
            )

            if issue.severity == ValidationSeverity.ERROR:
                errors.append(parser_issue)
            elif issue.severity == ValidationSeverity.WARNING:
                warnings.append(parser_issue)

    # Update preview
    return DOCXImportPreview(
        import_id=canonical.import_id,
        filename=original_preview.filename,
        academic_year=canonical.academic_year,
        department=original_preview.department,
        parser_status=original_preview.parser_status,
        physical_structure=original_preview.physical_structure,
        total_blocks=original_preview.total_blocks,
        resolved_count=len(resolved_activities),
        unresolved_count=len(original_preview.unresolved_blocks),
        manually_resolved_count=original_preview.manually_resolved_count,
        faculty_legend=original_preview.faculty_legend,
        resolved_activities=resolved_activities,
        unresolved_blocks=original_preview.unresolved_blocks,
        errors=errors,
        warnings=warnings,
    )
