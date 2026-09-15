"""Manual resolution logic for unresolved timetable blocks.

Staging-only logic - does not persist to database.
Applies user's explicit resolution decisions to unresolved blocks.
"""

from typing import Optional

from app.domain.timetable import SourceLocation, ValidationIssue, ValidationSeverity
from .staging import (
    DOCXImportPreview,
    UnresolvedTimetableBlock,
    ManualResolutionMapping,
    ResolvedActivity,
    ExcludedCandidate,
)


def apply_manual_resolution(
    preview: DOCXImportPreview,
    mapping: ManualResolutionMapping
) -> tuple[bool, Optional[str]]:
    """Apply manual resolution mapping to unresolved block.

    IMPORTANT: One unresolved block may produce multiple resolved activities.
    The block is NOT removed until explicitly marked complete or all candidates used.

    Multiple ManualResolutionMapping entries may reference the same source_block_temp_id.
    Each mapping creates one ResolvedActivity while preserving the source block.

    Args:
        preview: DOCXImportPreview containing unresolved blocks
        mapping: User's explicit resolution decision

    Returns:
        (success, error_message)
    """
    # Find the unresolved block
    target_block = None
    for block in preview.unresolved_blocks:
        if block.temp_id == mapping.source_block_temp_id:
            target_block = block
            break

    if not target_block:
        return False, f"Unresolved block {mapping.source_block_temp_id} not found"

    # Validate that selected candidates exist in the block
    activity_exists = any(
        ac["code"] == mapping.selected_activity
        for ac in target_block.activity_candidates
    )
    if not activity_exists:
        return False, f"Selected activity '{mapping.selected_activity}' not in block candidates"

    teacher_exists = any(
        tc["acronym"] == mapping.selected_teacher
        for tc in target_block.teacher_candidates
    )
    if not teacher_exists:
        return False, f"Selected teacher '{mapping.selected_teacher}' not in block candidates"

    if mapping.selected_resource:
        resource_exists = any(
            rc["code"] == mapping.selected_resource
            for rc in target_block.resource_candidates
        )
        if not resource_exists:
            return False, f"Selected resource '{mapping.selected_resource}' not in block candidates"

    # Determine entry_type from activity candidate
    activity_candidate = next(
        (ac for ac in target_block.activity_candidates if ac["code"] == mapping.selected_activity),
        None
    )
    entry_type = activity_candidate["inferred_type"] if activity_candidate else "CLASS"

    # Create resolved activity
    source_location = SourceLocation(
        source_type=target_block.source_location["source_type"],
        table_index=target_block.source_location.get("table_index"),
        table_row=target_block.source_location.get("table_row"),
        table_col=target_block.source_location.get("table_col"),
        display_context=f"Manual resolution of {target_block.day} {target_block.section} {target_block.slots}"
    )

    resolved = ResolvedActivity(
        day=target_block.day,
        section=target_block.section,
        slots=target_block.slots,
        entry_type=entry_type,
        subject_or_activity=mapping.selected_activity,
        teacher_acronym=mapping.selected_teacher,
        resource_code=mapping.selected_resource,
        source_location=source_location,
        original_text=target_block.original_text,
        manually_resolved=True
    )

    # Add to resolved blocks
    preview.resolved_blocks.append(resolved)

    # DO NOT REMOVE BLOCK YET - it may have more activities to resolve
    # Block will be removed when explicitly marked complete via finalize_block_resolution()

    preview.resolved_count += 1
    preview.manually_resolved_count += 1

    # Record the resolution
    preview.manual_resolutions.append(mapping)

    return True, None


def apply_multiple_resolutions(
    preview: DOCXImportPreview,
    mappings: list[ManualResolutionMapping]
) -> tuple[int, list[str]]:
    """Apply multiple manual resolution mappings.

    One unresolved block can generate multiple resolved activities by providing
    multiple mappings with the same source_block_temp_id.

    IMPORTANT: Blocks are NOT removed after each mapping. They remain until
    finalize_block_resolution() is called.

    Args:
        preview: DOCXImportPreview
        mappings: List of resolution mappings

    Returns:
        (success_count, error_messages)
    """
    success_count = 0
    errors = []

    for mapping in mappings:
        success, error = apply_manual_resolution(preview, mapping)
        if success:
            success_count += 1
        else:
            errors.append(error)

    return success_count, errors


def finalize_block_resolution(
    preview: DOCXImportPreview,
    block_temp_id: str
) -> tuple[bool, Optional[str]]:
    """Mark an unresolved block as fully resolved and remove it.

    Call this after all manual resolutions for a block are complete.
    Validates that the block has been addressed (either resolved or excluded).

    Args:
        preview: DOCXImportPreview
        block_temp_id: Block to finalize

    Returns:
        (success, error_message)
    """
    # Find the block
    target_block = None
    for block in preview.unresolved_blocks:
        if block.temp_id == block_temp_id:
            target_block = block
            break

    if not target_block:
        return False, f"Unresolved block {block_temp_id} not found"

    # Check if any resolutions were applied to this block
    resolutions_for_block = [
        r for r in preview.manual_resolutions
        if r.source_block_temp_id == block_temp_id
    ]

    if not resolutions_for_block:
        return False, f"No manual resolutions applied to block {block_temp_id}"

    # Remove block from unresolved
    preview.unresolved_blocks.remove(target_block)
    preview.unresolved_count -= 1

    return True, None


def exclude_candidate(
    preview: DOCXImportPreview,
    block_temp_id: str,
    candidate_type: str,
    candidate_code: str,
    exclusion_reason: str
) -> tuple[bool, Optional[str]]:
    """Mark a candidate as intentionally excluded.

    Excluded candidates remain in staging data for audit but are not used
    in resolution.

    Args:
        preview: DOCXImportPreview
        block_temp_id: Unresolved block ID
        candidate_type: "activity" | "teacher" | "resource"
        candidate_code: Candidate code to exclude
        exclusion_reason: Why this candidate was excluded

    Returns:
        (success, error_message)
    """
    # Find the block
    target_block = None
    for block in preview.unresolved_blocks:
        if block.temp_id == block_temp_id:
            target_block = block
            break

    if not target_block:
        return False, f"Unresolved block {block_temp_id} not found"

    # Verify candidate exists
    candidates_list = {
        "activity": target_block.activity_candidates,
        "teacher": target_block.teacher_candidates,
        "resource": target_block.resource_candidates,
    }.get(candidate_type)

    if not candidates_list:
        return False, f"Invalid candidate_type: {candidate_type}"

    # Check if candidate exists
    code_field = "code" if candidate_type in ["activity", "resource"] else "acronym"
    exists = any(c[code_field] == candidate_code for c in candidates_list)

    if not exists:
        return False, f"{candidate_type} candidate '{candidate_code}' not found in block"

    # Record exclusion
    exclusion = ExcludedCandidate(
        source_block_temp_id=block_temp_id,
        candidate_type=candidate_type,
        candidate_code=candidate_code,
        exclusion_reason=exclusion_reason
    )

    preview.excluded_candidates.append(exclusion)

    return True, None


def validate_resolution_completeness(preview: DOCXImportPreview) -> list[ValidationIssue]:
    """Validate that all unresolved blocks have been addressed.

    Distinguishes between:
    - Fully resolved: all candidates mapped
    - Partially resolved: some candidates mapped, block still unresolved
    - Unresolved: no resolutions applied
    - Intentionally excluded: marked as excluded

    Returns list of issues if unresolved blocks remain.

    Args:
        preview: DOCXImportPreview

    Returns:
        List of ValidationIssue objects
    """
    issues = []

    for block in preview.unresolved_blocks:
        # Check if any resolutions were applied to this block
        resolutions_for_block = [
            r for r in preview.manual_resolutions
            if r.source_block_temp_id == block.temp_id
        ]

        # Check if any candidates were excluded
        exclusions_for_block = [
            e for e in preview.excluded_candidates
            if e.source_block_temp_id == block.temp_id
        ]

        if resolutions_for_block:
            # Partially resolved - has some resolutions but block not finalized
            resolved_activities = {r.selected_activity for r in resolutions_for_block}
            resolved_teachers = {r.selected_teacher for r in resolutions_for_block}

            total_activities = len(block.activity_candidates)
            total_teachers = len(block.teacher_candidates)

            issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="PARTIALLY_RESOLVED_BLOCK",
                message=f"Block at {block.day} {block.section} {block.slots} is partially resolved: "
                        f"{len(resolved_activities)}/{total_activities} activities, "
                        f"{len(resolved_teachers)}/{total_teachers} teachers mapped. "
                        f"Call finalize_block_resolution() when complete.",
                affected_entities={
                    "day": block.day,
                    "section": block.section,
                    "slots": ",".join(block.slots),
                    "temp_id": block.temp_id
                },
                suggestion="Complete remaining mappings and call finalize_block_resolution()"
            ))
        elif exclusions_for_block:
            # Has exclusions but no resolutions
            issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="EXCLUDED_BLOCK_NOT_FINALIZED",
                message=f"Block at {block.day} {block.section} {block.slots} has excluded candidates "
                        f"but is not finalized",
                affected_entities={
                    "day": block.day,
                    "section": block.section,
                    "slots": ",".join(block.slots),
                    "temp_id": block.temp_id
                },
                suggestion="Call finalize_block_resolution() to remove this block"
            ))
        else:
            # Completely unresolved
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="UNRESOLVED_BLOCK_REMAINS",
                message=f"Block at {block.day} {block.section} {block.slots} remains unresolved: {block.ambiguity_reason}",
                affected_entities={
                    "day": block.day,
                    "section": block.section,
                    "slots": ",".join(block.slots),
                    "temp_id": block.temp_id
                },
                suggestion="Apply manual resolution mapping or exclude block from import"
            ))

    return issues
