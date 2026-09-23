"""Staging/transport data structures for DOCX import.

IMPORTANT: These are NOT persistent domain entities.
They are transport structures that live only during import session.
After conversion to CanonicalTimetable, these are discarded.

Architecture:
    DOCX -> ImportPreview (staging) -> CanonicalTimetable -> ValidationEngine -> DRAFT -> CONFIRMED
"""

from dataclasses import dataclass, field
from typing import Optional
import uuid

from app.domain.timetable import SourceLocation, ValidationIssue


# ---------------------------------------------------------------------------
# Occupant roles
# ---------------------------------------------------------------------------

class OccupantRole:
    """Role of a person found in a timetable cell."""
    FACULTY = "FACULTY"    # confirmed teaching staff (acronym in faculty legend)
    EXTERNAL = "EXTERNAL"  # industry / guest (e.g. Ind*)
    UNKNOWN = "UNKNOWN"    # token in cell but not resolvable from legend


# ---------------------------------------------------------------------------
# Candidate data classes
# ---------------------------------------------------------------------------

@dataclass
class ActivityCandidate:
    """A single activity candidate extracted from cell."""
    code: str                        # e.g. "PY1", "ADA 1, DT 3,4"
    inferred_type: str               # "CLASS" | "LAB" | "OTHER"
    is_tokenization_ambiguous: bool  # True if "DS 3,4" etc.


@dataclass
class TeacherCandidate:
    """A single teacher/person candidate extracted from cell.

    is_identity_resolvable — token found in document legend (acronym or name).
    role — FACULTY / EXTERNAL / UNKNOWN.
    is_name_only — True when legend entry has no acronym (e.g. Meghana row 15).
    raw_token — original token string from cell before normalization.
    """
    acronym: str            # Acronym or name-only token
    normalized_acronym: str # uppercase, trimmed
    is_identity_resolvable: bool = True
    role: str = OccupantRole.FACULTY
    is_name_only: bool = False
    raw_token: str = ""


@dataclass
class ResourceCandidate:
    """A single resource candidate extracted from cell.

    is_assignment_ambiguous:
      False — resource deterministically occupied (e.g. comma-list: both OCCUPIED)
      True  — genuine uncertainty (slash-list: one of N, unknown which)
    """
    code: str             # e.g. "LAB1A", "CA1"
    normalized_code: str  # uppercase, no spaces
    is_identity_resolvable: bool = True
    is_assignment_ambiguous: bool = False  # True for slash-separated alternatives


# ---------------------------------------------------------------------------
# Activity group — for multi-activity cells
# ---------------------------------------------------------------------------

@dataclass
class ActivityGroup:
    """One logical activity group within a complex multi-activity cell.

    Complex cells (separated by blank lines) contain multiple activity groups.
    Example:
      ELE-3 / (VK, RMR, RR) / (LAB 1A)
      [blank]
      BC (Theory) / (Meghana) / (CA1)
    -> Two ActivityGroup objects.
    """
    raw_text: str
    activity_candidates: list = field(default_factory=list)  # list[ActivityCandidate]
    teacher_candidates: list = field(default_factory=list)   # list[TeacherCandidate]
    resource_candidates: list = field(default_factory=list)  # list[ResourceCandidate]
    unknown_tokens: list = field(default_factory=list)       # list[str]


# ---------------------------------------------------------------------------
# Main timetable block
# ---------------------------------------------------------------------------

@dataclass
class TimetableBlock:
    """Intermediate representation of a timetable cell.

    Occupancy-first architecture.
    Transport structure - NOT a persistent domain entity.
    """
    # Structural metadata
    day: str
    section: str
    slots: list  # list[str]

    # Extracted candidates (merged from all activity groups)
    activity_candidates: list  # list[ActivityCandidate]
    teacher_candidates: list   # list[TeacherCandidate]
    resource_candidates: list  # list[ResourceCandidate]

    # Preserved activity groups for audit
    activity_groups: list = field(default_factory=list)  # list[ActivityGroup]

    # Tokens found in cell but not resolvable from legend (for audit)
    unknown_tokens: list = field(default_factory=list)  # list[str]

    # Resolution status (Occupancy-First)
    activity_semantic_status: str = "MISSING"       # RESOLVED | AMBIGUOUS | MISSING
    teacher_occupancy_status: str = "UNSPECIFIED"   # DETERMINISTIC | AMBIGUOUS | UNSPECIFIED
    resource_occupancy_status: str = "UNSPECIFIED"  # DETERMINISTIC | AMBIGUOUS | UNSPECIFIED

    # Occupancies
    teacher_allocations: list = field(default_factory=list)   # list[TeacherOccupancy]
    resource_allocations: list = field(default_factory=list)  # list[ResourceOccupancy]

    # Source traceability
    source_location: SourceLocation = field(default_factory=lambda: SourceLocation(source_type="DOCX"))
    original_text: str = ""

    # Activity participation policy (set by participation_policy module after parsing).
    # "FACULTY_MANAGED" = normal class/lab; teacher expected.
    # "STUDENT_MANAGED" = activity owned by students (Placement, VAC, etc.); no teacher by design.
    # "EXTERNAL" = industry/guest led.
    # "UNKNOWN" = not classified.
    participation_policy: str = "UNKNOWN"

    # Validation issues
    issues: list = field(default_factory=list)  # list[ValidationIssue]

    temp_id: str = field(default_factory=lambda: str(uuid.uuid4()))



# ---------------------------------------------------------------------------
# Manual resolution and exclusion records
# ---------------------------------------------------------------------------

@dataclass
class ManualResolutionMapping:
    """User's explicit resolution decision for a block requiring review."""
    source_block_temp_id: str
    selected_activity: str
    selected_teacher: str
    selected_resource: Optional[str]
    resolved_by: str
    resolution_notes: Optional[str] = None


@dataclass
class ExcludedCandidate:
    """Record of intentionally excluded candidate."""
    source_block_temp_id: str
    candidate_type: str
    candidate_code: str
    exclusion_reason: str


# ---------------------------------------------------------------------------
# Import preview — main staging container
# ---------------------------------------------------------------------------

@dataclass
class DOCXImportPreview:
    """Transport structure for DOCX import preview.
    IMPORTANT: NOT a persistent domain entity.
    """
    import_session_id: str
    academic_year: str
    department: str
    source_file: str

    occupancy_ready_blocks: list = field(default_factory=list)  # list[TimetableBlock]
    occupancy_review_blocks: list = field(default_factory=list)  # list[TimetableBlock]
    manual_resolutions: list = field(default_factory=list)  # list[ManualResolutionMapping]
    excluded_candidates: list = field(default_factory=list)  # list[ExcludedCandidate]

    errors: list = field(default_factory=list)    # list[ValidationIssue]
    warnings: list = field(default_factory=list)  # list[ValidationIssue]

    total_blocks: int = 0
    occupancy_ready_count: int = 0
    occupancy_review_count: int = 0
    manually_resolved_count: int = 0

    # Legends
    faculty_legend: dict = field(default_factory=dict)      # acronym -> full name
    faculty_name_only: dict = field(default_factory=dict)   # normalized_name -> full name
    faculty_external: dict = field(default_factory=dict)    # token -> description
    resource_legend: dict = field(default_factory=dict)     # code -> location

    # Occupancy extraction
    teacher_occupancies: list = field(default_factory=list)   # list[TeacherOccupancy]
    resource_occupancies: list = field(default_factory=list)  # list[ResourceOccupancy]
    occupancy_extraction_success_count: int = 0
    occupancy_extraction_blocked_count: int = 0
