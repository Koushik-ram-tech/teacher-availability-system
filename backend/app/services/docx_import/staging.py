"""Staging/transport data structures for DOCX import.

IMPORTANT: These are NOT persistent domain entities.
They are transport structures that live only during import session.
After conversion to CanonicalTimetable, these are discarded.

Architecture:
    DOCX → ImportPreview (staging) → CanonicalTimetable → ValidationEngine → DRAFT → CONFIRMED
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from app.domain.timetable import SourceLocation, ValidationIssue


@dataclass
class ActivityCandidate:
    """A single activity candidate extracted from cell.

    Transport structure - NOT a persistent domain entity.
    """
    code: str  # e.g., "PY1", "PE2", "DS 3,4", "PE 1,2,3,4"
    inferred_type: str  # "CLASS" | "LAB" | "OTHER"
    is_tokenization_ambiguous: bool  # True if phrase like "DS 3,4" or "PE 1,2,3,4" has unclear boundaries


@dataclass
class TeacherCandidate:
    """A single teacher candidate extracted from cell.

    Transport structure - NOT a persistent domain entity.

    is_identity_resolvable at PARSER level means:
    - The acronym was extracted unambiguously from document structure
    - Database identity resolution is NOT performed by parser
    - ValidationEngine will perform actual database teacher lookup
    """
    acronym: str  # e.g., "SU", "TS"
    normalized_acronym: str  # uppercase, trimmed
    is_identity_resolvable: bool = True  # Document-level: candidate extracted unambiguously


@dataclass
class ResourceCandidate:
    """A single resource candidate extracted from cell.

    Transport structure - NOT a persistent domain entity.

    is_identity_resolvable at PARSER level means:
    - The resource code was extracted unambiguously from document structure
    - Database identity resolution is NOT performed by parser
    - ValidationEngine will perform actual database resource lookup
    """
    code: str  # e.g., "LAB1A", "LAB1B", "CA1"
    normalized_code: str  # uppercase, spacing normalized
    is_identity_resolvable: bool = True  # Document-level: candidate extracted unambiguously


@dataclass
class TimetableBlock:
    """Intermediate representation of a timetable cell.

    Preserves individual candidates without asserting relationships.
    May be RESOLVED (1:1:1 with no ambiguity) or UNRESOLVED (ambiguous).

    Transport structure - NOT a persistent domain entity.
    """
    # Structural metadata
    day: str  # ISO day name: "monday", "tuesday", etc.
    section: str  # e.g., "I-A", "III-B"
    slots: list[str]  # e.g., ["S4"], ["S4", "S5"]

    # Extracted candidates (lists preserve individuals)
    activity_candidates: list[ActivityCandidate]
    teacher_candidates: list[TeacherCandidate]
    resource_candidates: list[ResourceCandidate]

    # Resolution status
    is_resolved: bool  # True if can convert to ScheduleActivity
    ambiguity_reason: Optional[str] = None  # Why unresolved, if applicable

    # Source traceability
    source_location: SourceLocation = field(default_factory=lambda: SourceLocation(source_type="DOCX"))
    original_text: str = ""  # Exact cell content

    # Validation issues accumulated during parsing
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass
class UnresolvedTimetableBlock:
    """Transport structure for unresolved block in import preview.

    IMPORTANT: This is NOT a persistent domain entity.
    Lives only in ImportPreview/staging state during import session.
    """
    temp_id: str  # Temporary ID for this import session
    day: str
    section: str
    slots: list[str]
    activity_candidates: list[dict]  # Serialized ActivityCandidate
    teacher_candidates: list[dict]  # Serialized TeacherCandidate
    resource_candidates: list[dict]  # Serialized ResourceCandidate
    ambiguity_reason: str
    original_text: str
    source_location: dict  # Serialized SourceLocation
    issues: list[dict]  # Serialized ValidationIssue


@dataclass
class ResolvedActivity:
    """A resolved activity ready for conversion to CanonicalTimetable.

    Transport structure - NOT a persistent domain entity.
    """
    day: str
    section: str
    slots: list[str]
    entry_type: str  # "CLASS" | "LAB" | "OTHER"
    subject_or_activity: str
    teacher_acronym: str
    resource_code: Optional[str]
    source_location: SourceLocation
    original_text: str = ""
    manually_resolved: bool = False


@dataclass
class ManualResolutionMapping:
    """User's explicit resolution decision for unresolved block.

    IMPORTANT: This is NOT a persistent domain entity.
    Applied to ImportPreview during import session, then discarded.
    """
    source_block_temp_id: str  # References UnresolvedTimetableBlock.temp_id
    selected_activity: str
    selected_teacher: str
    selected_resource: Optional[str]
    resolved_by: str  # Current user
    resolution_notes: Optional[str] = None


@dataclass
class ExcludedCandidate:
    """Record of intentionally excluded candidate.

    IMPORTANT: This is NOT a persistent domain entity.
    Tracked in ImportPreview for audit during import session.
    """
    source_block_temp_id: str
    candidate_type: str  # "activity"|"teacher"|"resource"
    candidate_code: str
    exclusion_reason: str


@dataclass
class DOCXImportPreview:
    """Transport structure for DOCX import preview.

    Contains both resolved and unresolved blocks.
    This entire structure is staging/transport data.

    IMPORTANT: NOT a persistent domain entity.
    """
    import_session_id: str  # Temporary session ID
    academic_year: str
    department: str
    source_file: str

    # Successfully resolved (can convert to CanonicalTimetable)
    resolved_blocks: list[ResolvedActivity] = field(default_factory=list)

    # Unresolved blocks (need manual resolution)
    unresolved_blocks: list[UnresolvedTimetableBlock] = field(default_factory=list)

    # Manual resolutions applied
    manual_resolutions: list[ManualResolutionMapping] = field(default_factory=list)

    # Excluded candidates
    excluded_candidates: list[ExcludedCandidate] = field(default_factory=list)

    # Validation
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    # Summary
    total_blocks: int = 0
    resolved_count: int = 0
    unresolved_count: int = 0
    manually_resolved_count: int = 0

    # Legends (for document-driven resolution)
    faculty_legend: dict[str, str] = field(default_factory=dict)  # acronym -> full name
    resource_legend: dict[str, str] = field(default_factory=dict)  # code -> full name/location

    # NEW: Occupancy extraction (Phase 1 - Availability)
    # Teacher and resource occupancy extracted independently of activity resolution
    # Subject ambiguity does NOT block occupancy when allocation is deterministic
    teacher_occupancies: list = field(default_factory=list)  # list[TeacherOccupancy] - using list to avoid circular import
    resource_occupancies: list = field(default_factory=list)  # list[ResourceOccupancy]

    # Occupancy extraction statistics
    occupancy_extraction_success_count: int = 0  # Blocks where occupancy extracted
    occupancy_extraction_blocked_count: int = 0  # Blocks where occupancy cannot be determined
