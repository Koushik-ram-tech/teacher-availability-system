"""
Pydantic schemas for the DOCX import workflow.

These schemas extend the base import workflow to support:
- DOCX-specific preview data (unresolved blocks)
- Manual resolution workflow
- DOCX parsing errors and warnings

DOCX workflow:
  POST /imports/docx → DOCXImportPreview
  POST /imports/{import_id}/resolve → DOCXImportPreview (after resolution)
  POST /imports/{import_id}/finalize → CanonicalTimetable (ready for confirm)
  POST /imports/{import_id}/confirm → ImportConfirmOut (reuses existing)
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# DOCX Parser Issues
# ---------------------------------------------------------------------------


class DOCXParserIssue(BaseModel):
    """Structured parser issue from DOCX parsing."""

    severity: str  # ERROR, WARNING
    code: str
    message: str
    source_location: str | None = None
    affected_entities: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# DOCX Unresolved Block Candidates
# ---------------------------------------------------------------------------


class ActivityCandidate(BaseModel):
    """One possible activity interpretation."""
    code: str
    description: str | None = None


class TeacherCandidate(BaseModel):
    """One possible teacher interpretation."""
    acronym: str
    name: str | None = None  # From faculty legend if available


class ResourceCandidate(BaseModel):
    """One possible resource interpretation."""
    code: str
    type: str | None = None  # LAB, CLASSROOM, FACILITY


# ---------------------------------------------------------------------------
# DOCX Unresolved Block
# ---------------------------------------------------------------------------


class UnresolvedBlock(BaseModel):
    """
    One timetable block that requires review.

    Occupancy-first model: The three dimensions are independent.
    - activity_semantic_status: RESOLVED | AMBIGUOUS | MISSING (subject interpretation)
    - teacher_occupancy_status: DETERMINISTIC | AMBIGUOUS | UNSPECIFIED
    - resource_occupancy_status: DETERMINISTIC | AMBIGUOUS | UNSPECIFIED

    resolution_required is True ONLY when teacher or resource occupancy is
    genuinely ambiguous. Activity semantic ambiguity alone does NOT require
    manual resolution to proceed to confirmation.

    IMPORTANT: These are transport-only structures, not persisted to DB.
    """

    block_id: str  # Unique identifier within this import
    day: str  # lowercase: monday, tuesday, etc.
    section: str
    slots: list[str]  # e.g. ["S4", "S5"]

    # Source location
    source_location: str
    table_row: int | None = None
    table_col: int | None = None

    # Candidates (NOT flattened)
    activity_candidates: list[ActivityCandidate] = Field(default_factory=list)
    teacher_candidates: list[TeacherCandidate] = Field(default_factory=list)
    resource_candidates: list[ResourceCandidate] = Field(default_factory=list)

    # Occupancy-first: three independent statuses
    activity_semantic_status: str = "MISSING"  # RESOLVED | AMBIGUOUS | MISSING
    teacher_occupancy_status: str = "UNSPECIFIED"  # DETERMINISTIC | AMBIGUOUS | UNSPECIFIED
    resource_occupancy_status: str = "UNSPECIFIED"  # DETERMINISTIC | AMBIGUOUS | UNSPECIFIED

    # Ambiguity reason (human-readable summary)
    ambiguity_reason: str
    # resolution_required = True only when teacher or resource allocation is genuinely ambiguous.
    # Activity semantic ambiguity alone does NOT set this to True.
    resolution_required: bool = False


# ---------------------------------------------------------------------------
# DOCX Resolved Activity (before canonical conversion)
# ---------------------------------------------------------------------------


class ResolvedActivity(BaseModel):
    """
    One resolved timetable activity from DOCX parsing.

    This is the DOCX-specific representation before conversion
    to CanonicalTimetable.ScheduleActivity.
    """

    day: str
    section: str
    slots: list[str]
    teacher_acronym: str
    subject_or_activity: str
    resource_code: str | None = None
    source_location: str
    entry_type: str = "CLASS"  # CLASS, LAB, OTHER

    # Metadata
    is_multi_slot: bool = False
    is_manually_resolved: bool = False


# ---------------------------------------------------------------------------
# Faculty Legend
# ---------------------------------------------------------------------------


class FacultyLegendEntry(BaseModel):
    """One entry from DOCX faculty legend table."""

    acronym: str
    full_name: str


# ---------------------------------------------------------------------------
# DOCX Import Preview
# ---------------------------------------------------------------------------


class DOCXImportPreview(BaseModel):
    """
    Complete DOCX import preview including resolved and unresolved data.

    This extends the base ImportPreview concept with DOCX-specific
    structures for handling ambiguous blocks.
    """

    import_id: str
    filename: str
    academic_year: str
    department: str

    # Parser metadata
    parser_status: str  # COMPLETE, PARTIAL, FAILED
    physical_structure: dict[str, int | str] = Field(default_factory=dict)

    # Parsing results
    total_blocks: int
    resolved_count: int
    unresolved_count: int
    manually_resolved_count: int = 0

    # Faculty legend
    faculty_legend: list[FacultyLegendEntry] = Field(default_factory=list)

    # Resolved activities (ready for canonical conversion)
    resolved_activities: list[ResolvedActivity] = Field(default_factory=list)

    # Unresolved blocks (require manual resolution)
    unresolved_blocks: list[UnresolvedBlock] = Field(default_factory=list)

    # Issues
    errors: list[DOCXParserIssue] = Field(default_factory=list)
    warnings: list[DOCXParserIssue] = Field(default_factory=list)

    def can_convert_to_canonical(self) -> bool:
        """Check if all required blocks are resolved."""
        return all(not block.resolution_required for block in self.unresolved_blocks)

    def has_errors(self) -> bool:
        """Check if any ERROR-level issues exist."""
        return len(self.errors) > 0


# ---------------------------------------------------------------------------
# Manual Resolution Input
# ---------------------------------------------------------------------------


class ManualResolutionInput(BaseModel):
    """
    User's explicit resolution of one ambiguous block.

    Maps one unresolved block to one specific activity.
    Multiple resolutions may reference the same block_id
    (one block → multiple activities).
    """

    block_id: str

    # Explicit selections (by code/acronym)
    selected_activity: str
    selected_teacher: str
    selected_resource: str | None = None

    # Optional metadata
    entry_type: str = "CLASS"  # CLASS, LAB, OTHER
    notes: str | None = None


class BulkManualResolutionInput(BaseModel):
    """Multiple manual resolutions applied together."""

    resolutions: list[ManualResolutionInput]


class FinalizeBlockInput(BaseModel):
    """
    Mark one or more blocks as finalized (either resolved or intentionally excluded).

    After finalization, the block is removed from unresolved_blocks.
    """

    block_ids: list[str]


# ---------------------------------------------------------------------------
# Manual Resolution Response
# ---------------------------------------------------------------------------


class ManualResolutionResult(BaseModel):
    """Result of applying manual resolutions."""

    applied_count: int
    remaining_unresolved: int
    new_resolved_activities: list[ResolvedActivity] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
