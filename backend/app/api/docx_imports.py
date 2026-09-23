"""
docx_imports.py — FastAPI router for DOCX timetable import.

Endpoints (all under /api/v1):

  POST   /imports/docx                      Upload .docx → parse → stage → return preview
  POST   /imports/{import_id}/resolve       Apply manual resolution to unresolved blocks
  POST   /imports/{import_id}/finalize      Mark blocks as finalized/excluded
  POST   /imports/{import_id}/confirm       Convert to canonical → validate → persist

Staging uses the same module-level in-memory dict as XLSX imports.
DOCX preview data is stored alongside CanonicalTimetable for manual resolution workflow.

HTTP status codes follow existing project conventions:
  400 = malformed DOCX / bad request
  404 = import_id not found
  422 = semantic validation failure (errors prevent confirmation)
  500 = unexpected server error (all changes rolled back)
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.api import staging
from app.domain.docx_converters import canonical_to_docx_preview, docx_preview_to_canonical
from app.domain.timetable import CanonicalTimetable
from app.domain.validation import validate_canonical
from app.schemas.docx_imports import (
    BulkManualResolutionInput,
    DOCXImportPreview,
    DOCXParserIssue,
    FacultyLegendEntry,
    FinalizeBlockInput,
    ManualResolutionResult,
    ResolvedActivity,
    UnresolvedBlock,
    ActivityCandidate,
    TeacherCandidate,
    ResourceCandidate,
)
from app.schemas.imports import ImportConfirmOut
from app.services.docx_import.manual_resolution import (
    apply_manual_resolution,
    finalize_block_resolution,
)
from app.services.docx_import.parser import parse_docx_timetable
from app.services.excel_import.persister import PersistenceError, persist_import

router = APIRouter(prefix="/imports", tags=["docx_imports"])

MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024  # 10 MB


# ---------------------------------------------------------------------------
# POST /imports/docx
# ---------------------------------------------------------------------------


@router.post(
    "/docx",
    status_code=status.HTTP_200_OK,
    response_model=DOCXImportPreview,
    summary="Upload a DOCX timetable for preview (no DB writes)",
)
async def upload_docx(
    file: Annotated[UploadFile, File(description="DOCX timetable document")],
    academic_year: Annotated[str, Form(description="Academic year e.g. 2026-Odd")],
    department: Annotated[str, Form(description="Department name e.g. Computer Applications")],
    db: Session = Depends(get_db),
) -> DOCXImportPreview:
    """
    Parse an uploaded .docx timetable, validate structure, and return a preview.

    The preview includes:
    - Resolved activities (ready for canonical conversion)
    - Unresolved blocks (require manual resolution)
    - Faculty legend
    - Parser errors and warnings

    No timetable rows are written to the database at this stage.

    The returned ``import_id`` is used with resolution and confirm endpoints.
    """
    # --- File type guard ---
    filename: str = file.filename or ""
    if not filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .docx files are accepted.",
        )

    data: bytes = await file.read()

    if len(data) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )
    if len(data) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {mb} MB.",
        )

    # --- Parse DOCX ---
    try:
        parser_preview = parse_docx_timetable(
            docx_bytes=data,
            department=department.strip(),
            academic_year=academic_year.strip(),
            source_file=filename,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"DOCX parsing failed: {str(exc)}",
        ) from exc

    # --- Convert to API schema ---
    api_preview = _convert_parser_to_api_preview(
        parser_preview=parser_preview,
        filename=filename,
        department=department.strip(),
        academic_year=academic_year.strip(),
    )

    # --- Stage DOCX preview ---
    staging.stage_docx_preview(api_preview.import_id, api_preview)

    return api_preview


# ---------------------------------------------------------------------------
# POST /imports/{import_id}/resolve
# ---------------------------------------------------------------------------


@router.post(
    "/{import_id}/resolve",
    status_code=status.HTTP_200_OK,
    response_model=ManualResolutionResult,
    summary="Apply manual resolution to unresolved blocks",
)
def resolve_blocks(
    import_id: str,
    resolution_input: BulkManualResolutionInput,
    db: Session = Depends(get_db),
) -> ManualResolutionResult:
    """
    Apply explicit manual resolutions to unresolved blocks.

    Multiple resolutions may reference the same block_id (one block → multiple activities).
    Blocks remain in unresolved_blocks until explicitly finalized.

    Returns the updated count and any errors encountered.
    """
    # --- Retrieve staged DOCX preview ---
    preview = staging.get_docx_preview(import_id)
    if preview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Import '{import_id}' not found. "
                "It may have expired, been confirmed, or never existed."
            ),
        )

    # --- Apply resolutions ---
    applied_count = 0
    new_activities: list[ResolvedActivity] = []
    errors: list[str] = []

    # Convert preview back to parser format for resolution
    from app.services.docx_import.staging import ManualResolutionMapping

    # Reconstruct unresolved blocks
    unresolved_blocks_map = {block.block_id: block for block in preview.unresolved_blocks}

    for resolution in resolution_input.resolutions:
        block_data = unresolved_blocks_map.get(resolution.block_id)
        if block_data is None:
            errors.append(f"Block '{resolution.block_id}' not found")
            continue

        # Create resolved activity.
        # teacher_acronym: empty string ("") = intentionally no teacher (student-managed).
        # For student-managed blocks, selected_teacher may be None/empty — that is valid.
        resolved = ResolvedActivity(
            day=block_data.day,
            section=block_data.section,
            slots=block_data.slots,
            teacher_acronym=resolution.selected_teacher or "",
            subject_or_activity=resolution.selected_activity,
            resource_code=resolution.selected_resource,
            source_location=block_data.source_location,
            entry_type=resolution.entry_type,
            is_multi_slot=len(block_data.slots) > 1,
            is_manually_resolved=True,
        )

        preview.resolved_activities.append(resolved)
        new_activities.append(resolved)
        applied_count += 1

    # Update counts
    preview.resolved_count = len(preview.resolved_activities)
    preview.manually_resolved_count += applied_count

    # Update staging
    staging.update_docx_preview(import_id, preview)

    return ManualResolutionResult(
        applied_count=applied_count,
        remaining_unresolved=preview.unresolved_count,
        new_resolved_activities=new_activities,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# POST /imports/{import_id}/finalize
# ---------------------------------------------------------------------------


@router.post(
    "/{import_id}/finalize",
    status_code=status.HTTP_200_OK,
    response_model=DOCXImportPreview,
    summary="Finalize blocks (mark as complete or excluded)",
)
def finalize_blocks(
    import_id: str,
    finalize_input: FinalizeBlockInput,
    db: Session = Depends(get_db),
) -> DOCXImportPreview:
    """
    Mark blocks as finalized, removing them from unresolved_blocks.

    This allows blocks to be:
    - Fully resolved (all activities mapped)
    - Intentionally excluded (not imported)

    After finalization, the import can proceed to confirmation if all
    required blocks are resolved.
    """
    # --- Retrieve staged DOCX preview ---
    preview = staging.get_docx_preview(import_id)
    if preview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Import '{import_id}' not found.",
        )

    # --- Remove finalized blocks ---
    remaining_blocks = [
        block for block in preview.unresolved_blocks
        if block.block_id not in finalize_input.block_ids
    ]

    removed_count = len(preview.unresolved_blocks) - len(remaining_blocks)
    preview.unresolved_blocks = remaining_blocks
    preview.unresolved_count = len(remaining_blocks)

    # Update staging
    staging.update_docx_preview(import_id, preview)

    return preview


# ---------------------------------------------------------------------------
# Helper: Convert parser preview to API preview
# ---------------------------------------------------------------------------


def _convert_parser_to_api_preview(
    parser_preview,
    filename: str,
    department: str,
    academic_year: str,
) -> DOCXImportPreview:
    """Convert internal parser preview to API schema."""

    # Convert faculty legend
    faculty_legend = [
        FacultyLegendEntry(acronym=acronym, full_name=name)
        for acronym, name in parser_preview.faculty_legend.items()
    ]

    # Convert resolved blocks (from occupancy_ready_blocks)
    resolved_activities = []
    for block in parser_preview.occupancy_ready_blocks:
        # Convert source_location to string if it's an object
        source_loc_str = block.source_location
        if hasattr(block.source_location, 'to_display'):
            source_loc_str = block.source_location.to_display()
        elif not isinstance(block.source_location, str):
            source_loc_str = str(block.source_location)

        combined_activity = ", ".join(ac.code for ac in block.activity_candidates) or "Unknown Activity"
        entry_type = block.activity_candidates[0].inferred_type if block.activity_candidates else "CLASS"
        combined_resource = ", ".join(rc.code for rc in block.resource_candidates) if block.resource_candidates else None
        combined_resource_codes = [rc.code for rc in block.resource_candidates]

        # Produce one resolved entry per FACULTY/NAME_ONLY teacher.
        # EXTERNAL participants (Ind*, industry persons) are not faculty teachers —
        # they are preserved in the notes field and do NOT create Teacher DB rows.
        faculty_teachers = [
            t for t in block.teacher_candidates
            if getattr(t, "role", "FACULTY") in ("FACULTY",) or getattr(t, "is_name_only", False)
        ]
        external_participants = [
            t for t in block.teacher_candidates
            if getattr(t, "role", "FACULTY") == "EXTERNAL"
        ]
        external_notes = (
            "External: " + ", ".join(t.raw_token or t.acronym for t in external_participants)
            if external_participants else None
        )

        # ---------------------------------------------------------------
        # Multi-group cells: emit one activity per teacher per group,
        # linked to the group-specific resource. No cross-group association.
        # Single-group cells: existing flat behavior unchanged.
        # ---------------------------------------------------------------
        groups = getattr(block, 'activity_groups', [])

        if len(groups) > 1:
            for group in groups:
                grp_activity = (
                    ", ".join(ac.code for ac in group.activity_candidates)
                    or combined_activity
                )
                grp_entry_type = (
                    group.activity_candidates[0].inferred_type
                    if group.activity_candidates else entry_type
                )
                # resource_code: comma-joined provenance text
                # resource_codes: individual codes for separate persistence
                grp_resource_text = (
                    ", ".join(rc.code for rc in group.resource_candidates)
                    if group.resource_candidates else None
                )
                grp_resource_codes = [rc.code for rc in group.resource_candidates]
                grp_faculty = [
                    t for t in group.teacher_candidates
                    if getattr(t, "role", "FACULTY") in ("FACULTY",) or getattr(t, "is_name_only", False)
                ]
                grp_external = [
                    t for t in group.teacher_candidates
                    if getattr(t, "role", "FACULTY") == "EXTERNAL"
                ]
                grp_notes = (
                    "External: " + ", ".join(t.raw_token or t.acronym for t in grp_external)
                    if grp_external else None
                )
                if grp_faculty:
                    for teacher in grp_faculty:
                        resolved_activities.append(ResolvedActivity(
                            day=block.day, section=block.section, slots=block.slots,
                            teacher_acronym=teacher.normalized_acronym,
                            subject_or_activity=grp_activity,
                            resource_code=grp_resource_text,
                            resource_codes=grp_resource_codes,
                            source_location=source_loc_str, entry_type=grp_entry_type,
                            is_multi_slot=len(block.slots) > 1, is_manually_resolved=False,
                            notes=grp_notes,
                        ))
                elif grp_external:
                    resolved_activities.append(ResolvedActivity(
                        day=block.day, section=block.section, slots=block.slots,
                        teacher_acronym="", subject_or_activity=grp_activity,
                        resource_code=grp_resource_text,
                        resource_codes=grp_resource_codes,
                        source_location=source_loc_str,
                        entry_type=grp_entry_type, is_multi_slot=len(block.slots) > 1,
                        is_manually_resolved=False, notes=grp_notes,
                    ))
                else:
                    resolved_activities.append(ResolvedActivity(
                        day=block.day, section=block.section, slots=block.slots,
                        teacher_acronym="", subject_or_activity=grp_activity,
                        resource_code=grp_resource_text,
                        resource_codes=grp_resource_codes,
                        source_location=source_loc_str,
                        entry_type=grp_entry_type, is_multi_slot=len(block.slots) > 1,
                        is_manually_resolved=False,
                    ))
        else:
            # Single-group (most common): existing flat path
            if faculty_teachers:
                for teacher in faculty_teachers:
                    resolved = ResolvedActivity(
                        day=block.day,
                        section=block.section,
                        slots=block.slots,
                        teacher_acronym=teacher.normalized_acronym,
                        subject_or_activity=combined_activity,
                        resource_code=combined_resource,
                        resource_codes=combined_resource_codes,
                        source_location=source_loc_str,
                        entry_type=entry_type,
                        is_multi_slot=len(block.slots) > 1,
                        is_manually_resolved=False,
                        notes=external_notes,
                    )
                    resolved_activities.append(resolved)
            elif external_participants:
                # External-only block: no faculty teacher but external participants present.
                # Preserve resource occupancy; notes carry the external identity.
                resolved = ResolvedActivity(
                    day=block.day,
                    section=block.section,
                    slots=block.slots,
                    teacher_acronym="",
                    subject_or_activity=combined_activity,
                    resource_code=combined_resource,
                    resource_codes=combined_resource_codes,
                    source_location=source_loc_str,
                    entry_type=entry_type,
                    is_multi_slot=len(block.slots) > 1,
                    is_manually_resolved=False,
                    notes=external_notes,
                )
                resolved_activities.append(resolved)
            else:
                # Resource-only block (no faculty teachers, no external): preserve resource occupancy
                # without inventing a teacher. teacher_acronym="" = unspecified.
                resolved = ResolvedActivity(
                    day=block.day,
                    section=block.section,
                    slots=block.slots,
                    teacher_acronym="",
                    subject_or_activity=combined_activity,
                    resource_code=combined_resource,
                    resource_codes=combined_resource_codes,
                    source_location=source_loc_str,
                    entry_type=entry_type,
                    is_multi_slot=len(block.slots) > 1,
                    is_manually_resolved=False,
                )
                resolved_activities.append(resolved)


    # Convert unresolved blocks (from occupancy_review_blocks)
    unresolved_blocks = []
    for block in parser_preview.occupancy_review_blocks:
        # Convert source_location to string
        source_loc_str = block.source_location
        if isinstance(block.source_location, dict):
            # Reconstruct from dict
            source_loc_str = f"DOCX: table {block.source_location.get('table_index', 0)} row {block.source_location.get('table_row', 0)} col {block.source_location.get('table_col', 0)}"
        elif hasattr(block.source_location, 'to_display'):
            source_loc_str = block.source_location.to_display()
        elif not isinstance(block.source_location, str):
            source_loc_str = str(block.source_location)

        # Convert candidates
        activity_candidates = [
            ActivityCandidate(code=c.code, description=None)
            for c in block.activity_candidates
        ]
        teacher_candidates = [
            TeacherCandidate(
                acronym=c.acronym,
                name=parser_preview.faculty_legend.get(c.acronym)
            )
            for c in block.teacher_candidates
        ]
        resource_candidates = [
            ResourceCandidate(code=c.code, type=None)
            for c in block.resource_candidates
        ]

        # Compute ambiguity reason and resolution_required based on occupancy statuses.
        # STUDENT_MANAGED: teacher absence is intentional — not flagged as a problem.
        participation_policy = getattr(block, 'participation_policy', 'UNKNOWN')
        is_student_managed_block = (participation_policy == "STUDENT_MANAGED")

        reasons = []
        resolution_required = False
        if block.teacher_occupancy_status == "AMBIGUOUS":
            reasons.append("teacher allocation ambiguous")
            resolution_required = True
        elif block.teacher_occupancy_status == "UNSPECIFIED" and not is_student_managed_block:
            # Only flag "no teacher" as a concern for faculty-managed activities.
            reasons.append("no teacher specified")
        elif block.teacher_occupancy_status == "UNSPECIFIED" and is_student_managed_block:
            reasons.append("student-managed activity (no faculty teacher by design)")
        if block.resource_occupancy_status == "AMBIGUOUS":
            reasons.append("resource allocation ambiguous")
            resolution_required = True
        elif block.resource_occupancy_status == "UNSPECIFIED" and len(block.resource_candidates) > 0:
            reasons.append("resource unresolved")
        # Extraction blocked when identity errors prevent occupancy extraction
        error_codes = {issue.code for issue in block.issues}
        extraction_blocked = (
            "UNRESOLVED_TEACHER_IDENTITY" in error_codes or
            "UNRESOLVED_RESOURCE_IDENTITY" in error_codes
        )
        if extraction_blocked:
            reasons.append("occupancy extraction blocked")
            resolution_required = True
        if block.activity_semantic_status in ("AMBIGUOUS", "MISSING"):
            reasons.append(f"activity: {block.activity_semantic_status.lower()}")
            # NOTE: activity ambiguity does NOT set resolution_required

        ambiguity_reason = ", ".join(reasons) if reasons else "review required"

        unresolved = UnresolvedBlock(
            block_id=block.temp_id,
            day=block.day,
            section=block.section,
            slots=block.slots,
            source_location=source_loc_str,
            activity_candidates=activity_candidates,
            teacher_candidates=teacher_candidates,
            resource_candidates=resource_candidates,
            activity_semantic_status=block.activity_semantic_status,
            teacher_occupancy_status=block.teacher_occupancy_status,
            resource_occupancy_status=block.resource_occupancy_status,
            participation_policy=participation_policy,
            is_student_managed=is_student_managed_block,
            ambiguity_reason=ambiguity_reason,
            resolution_required=resolution_required,
        )
        unresolved_blocks.append(unresolved)

    # Convert parser errors
    errors = [
        DOCXParserIssue(
            severity="ERROR",
            code=error.code,
            message=error.message,
            source_location=error.source_locations[0].to_display() if error.source_locations else None,
            affected_entities=error.affected_entities,
        )
        for error in parser_preview.errors
    ]

    # Determine parser status
    parser_status = "COMPLETE"
    if unresolved_blocks:
        parser_status = "PARTIAL"
    # Note: We don't set FAILED here because parsing succeeded.
    # FAILED should only be used when DOCX structure couldn't be parsed at all.

    # Physical structure info
    physical_structure = {
        "tables": getattr(parser_preview, 'physical_info', {}).get("table_count", 2),
        "main_table_rows": getattr(parser_preview, 'physical_info', {}).get("main_table_rows", 26),
        "main_table_cols": getattr(parser_preview, 'physical_info', {}).get("main_table_cols", 15),
    }

    return DOCXImportPreview(
        import_id=str(uuid.uuid4()),
        filename=filename,
        academic_year=academic_year,
        department=department,
        parser_status=parser_status,
        physical_structure=physical_structure,
        total_blocks=parser_preview.total_blocks,
        resolved_count=parser_preview.occupancy_ready_count,
        unresolved_count=parser_preview.occupancy_review_count,
        manually_resolved_count=parser_preview.manually_resolved_count,
        faculty_legend=faculty_legend,
        resolved_activities=resolved_activities,
        unresolved_blocks=unresolved_blocks,
        errors=errors,
        warnings=[],
    )
