"""Occupancy extraction for teacher and resource availability.

IMPORTANT PRODUCT REQUIREMENT:
The core purpose is determining BUSY/FREE slots, NOT teacher→subject relationships.

Architecture:
    TimetableBlock → OccupancyExtractor → Teacher/Resource Occupancy Records

Subject/activity tokenization ambiguity must NOT block occupancy extraction
when teacher/resource allocation is deterministic.

SAFETY RULE:
Only mark OCCUPIED when document structure deterministically supports the allocation.
NO guessing, NO "first teacher" behavior, NO proximity inference.
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from .staging import TimetableBlock, TeacherCandidate, ResourceCandidate
from app.domain.timetable import SourceLocation, ValidationSeverity


class OccupancyStatus(str, Enum):
    """Occupancy status for a teacher/resource in a specific slot."""
    OCCUPIED = "OCCUPIED"  # Deterministically allocated to this slot
    AMBIGUOUS = "AMBIGUOUS"  # Document structure unclear about allocation
    # FREE is derived later: working_slots - occupied_slots


@dataclass
class TeacherOccupancy:
    """Teacher occupancy record for a specific slot.

    Transport structure for availability extraction.
    NOT a persistent domain entity at this stage.

    Represents: "Teacher X is OCCUPIED during day Y, slot Z"
    Does NOT represent: "Teacher X teaches Subject S"
    """
    teacher_acronym: str  # Normalized acronym (e.g., "DNS", "SU")
    day: str  # ISO day name: "monday", "tuesday", etc.
    slot: str  # Single slot code: "S1", "S2", etc.
    status: OccupancyStatus  # OCCUPIED or AMBIGUOUS

    # Source traceability
    source_location: SourceLocation
    source_block_original_text: str = ""

    # Confidence/reason tracking
    extraction_reason: str = ""  # Why this occupancy was determined

    def __hash__(self):
        """Allow use in sets for deduplication."""
        return hash((self.teacher_acronym, self.day, self.slot))

    def __eq__(self, other):
        """Equality for deduplication."""
        if not isinstance(other, TeacherOccupancy):
            return False
        return (self.teacher_acronym == other.teacher_acronym and
                self.day == other.day and
                self.slot == other.slot)


@dataclass
class ResourceOccupancy:
    """Resource occupancy record for a specific slot.

    Transport structure for availability extraction.
    NOT a persistent domain entity at this stage.

    Represents: "Resource R is OCCUPIED during day Y, slot Z"
    Does NOT represent: "Resource R is used for Subject S"
    """
    resource_code: str  # Normalized code (e.g., "LAB1A", "CA1")
    day: str  # ISO day name
    slot: str  # Single slot code
    status: OccupancyStatus  # OCCUPIED or AMBIGUOUS

    # Source traceability
    source_location: SourceLocation
    source_block_original_text: str = ""

    # Confidence/reason tracking
    extraction_reason: str = ""

    def __hash__(self):
        """Allow use in sets for deduplication."""
        return hash((self.resource_code, self.day, self.slot))

    def __eq__(self, other):
        """Equality for deduplication."""
        if not isinstance(other, ResourceOccupancy):
            return False
        return (self.resource_code == other.resource_code and
                self.day == other.day and
                self.slot == other.slot)


@dataclass
class OccupancyExtractionResult:
    """Result of occupancy extraction from a TimetableBlock."""
    teacher_occupancies: list[TeacherOccupancy] = field(default_factory=list)
    resource_occupancies: list[ResourceOccupancy] = field(default_factory=list)
    extraction_successful: bool = True
    blocked_reason: Optional[str] = None


class OccupancyExtractor:
    """Extract teacher/resource occupancy independently of subject resolution.

    CORE PRINCIPLE:
    Subject/activity ambiguity does NOT prevent occupancy extraction when
    teacher/resource allocation is deterministic from document structure.

    Example:
        Block: "PY1, PE2, DS 3,4" with teachers (SU) (TS) (SS, KPS)

        Subject tokenization: AMBIGUOUS (cannot split)
        Teacher allocation: DETERMINISTIC (all 4 teachers allocated to block)

        Result: Extract occupancy for all 4 teachers
        Do NOT attempt to pair teachers with subjects
    """

    # Slot codes that represent breaks (not working availability)
    BREAK_SLOTS = {"BREAK"}

    @classmethod
    def extract_occupancy(cls, block: TimetableBlock) -> OccupancyExtractionResult:
        """Extract teacher and resource occupancy from TimetableBlock.

        Args:
            block: Parsed timetable block

        Returns:
            OccupancyExtractionResult with teacher and resource occupancies
        """
        result = OccupancyExtractionResult()

        # Extract teacher occupancy
        teacher_result = cls._extract_teacher_occupancy(block)
        result.teacher_occupancies = teacher_result.teacher_occupancies

        # Extract resource occupancy
        resource_result = cls._extract_resource_occupancy(block)
        result.resource_occupancies = resource_result.resource_occupancies

        # Overall extraction success logic:
        # - If teacher extraction attempted and failed → overall FAILED
        # - If resource extraction attempted and failed → overall FAILED
        # - Otherwise → overall SUCCESS

        # Check if extraction was attempted and failed
        teacher_failed = not teacher_result.extraction_successful
        resource_failed = not resource_result.extraction_successful

        if teacher_failed:
            result.extraction_successful = False
            result.blocked_reason = teacher_result.blocked_reason
            return result

        if resource_failed:
            result.extraction_successful = False
            result.blocked_reason = resource_result.blocked_reason
            return result

        # Both succeeded (or weren't needed)
        result.extraction_successful = True
        return result

    @classmethod
    def _extract_teacher_occupancy(cls, block: TimetableBlock) -> OccupancyExtractionResult:
        """Extract teacher occupancy from block.

        IMPORTANT: Subject/activity ambiguity does NOT block extraction when
        teacher allocation is deterministic.

        Returns OCCUPIED when:
        - Teachers are explicitly present in document structure
        - Teacher allocation to the block is deterministic

        Returns AMBIGUOUS when:
        - Multiple teachers present but unclear which are allocated
        - Document structure provides alternatives rather than allocations

        Does NOT extract when:
        - No teachers present (MISSING_TEACHER)
        - Teacher identity unresolvable
        """
        result = OccupancyExtractionResult()

        # Check for genuine teacher allocation issues
        error_codes = {
            issue.code
            for issue in block.issues
            if issue.severity == ValidationSeverity.ERROR
        }

        # Case 1: No teachers present
        if len(block.teacher_candidates) == 0:
            result.extraction_successful = False
            result.blocked_reason = "MISSING_TEACHER: No teachers specified"
            return result

        # Case 2: Unresolvable teacher identity
        if 'UNRESOLVED_TEACHER_IDENTITY' in error_codes:
            result.extraction_successful = False
            result.blocked_reason = "UNRESOLVED_TEACHER_IDENTITY: Cannot resolve teacher acronym"
            return result

        # Case 3: Genuinely ambiguous teacher mapping
        # This means multiple teachers but document structure doesn't establish
        # which teachers are actually allocated vs alternatives
        if 'AMBIGUOUS_TEACHER_MAPPING' in error_codes:
            # Check if this is truly ambiguous or just multiple teachers on same block
            # For now, we extract with AMBIGUOUS status if there's structural ambiguity

            # If the error was raised, it means ResolutionRule found ambiguity
            # However, for OCCUPANCY, we need to determine if ALL teachers are
            # allocated to the block (even if we don't know which→which subject)

            # CONSERVATIVE: If AMBIGUOUS_TEACHER_MAPPING raised, the document
            # structure doesn't clearly establish allocation
            result.extraction_successful = False
            result.blocked_reason = "AMBIGUOUS_TEACHER_MAPPING: Unclear which teachers allocated"
            return result

        # Case 4: Activity tokenization ambiguous BUT teachers deterministic
        # This is the KEY CASE where we extract occupancy
        # Example: "PY1, PE2, DS 3,4" with (SU) (TS) (SS, KPS)
        # All 4 teachers are allocated to this block - mark all OCCUPIED

        if 'ACTIVITY_TOKENIZATION_AMBIGUOUS' in error_codes:
            # Subject is ambiguous but teacher allocation is clear
            # Extract occupancy for ALL teachers in the block
            extraction_reason = "Activity tokenization ambiguous but teacher allocation deterministic"

            for teacher_candidate in block.teacher_candidates:
                occupancies = cls._create_teacher_occupancies(
                    teacher_candidate=teacher_candidate,
                    day=block.day,
                    slots=block.slots,
                    status=OccupancyStatus.OCCUPIED,
                    source_location=block.source_location,
                    original_text=block.original_text,
                    extraction_reason=extraction_reason
                )
                result.teacher_occupancies.extend(occupancies)

            result.extraction_successful = True
            return result

        # Case 5: Multiple teachers with one activity
        # If there's only one activity but multiple teachers, they might all
        # be team-teaching, or it might be ambiguous
        if len(block.teacher_candidates) > 1 and len(block.activity_candidates) == 1:
            # Check if AMBIGUOUS_TEACHER_MAPPING was raised
            # If not raised, assume all teachers allocated to the single activity
            extraction_reason = "Multiple teachers allocated to block"

            for teacher_candidate in block.teacher_candidates:
                occupancies = cls._create_teacher_occupancies(
                    teacher_candidate=teacher_candidate,
                    day=block.day,
                    slots=block.slots,
                    status=OccupancyStatus.OCCUPIED,
                    source_location=block.source_location,
                    original_text=block.original_text,
                    extraction_reason=extraction_reason
                )
                result.teacher_occupancies.extend(occupancies)

            result.extraction_successful = True
            return result

        # Case 6: Standard resolved or single teacher
        # One teacher, one activity, or already resolved
        if len(block.teacher_candidates) >= 1:
            extraction_reason = "Teacher allocation deterministic from document structure"

            for teacher_candidate in block.teacher_candidates:
                occupancies = cls._create_teacher_occupancies(
                    teacher_candidate=teacher_candidate,
                    day=block.day,
                    slots=block.slots,
                    status=OccupancyStatus.OCCUPIED,
                    source_location=block.source_location,
                    original_text=block.original_text,
                    extraction_reason=extraction_reason
                )
                result.teacher_occupancies.extend(occupancies)

            result.extraction_successful = True
            return result

        # Default: extraction failed
        result.extraction_successful = False
        result.blocked_reason = "Unknown teacher allocation scenario"
        return result

    @classmethod
    def _extract_resource_occupancy(cls, block: TimetableBlock) -> OccupancyExtractionResult:
        """Extract resource occupancy from block.

        IMPORTANT: Subject/activity ambiguity does NOT block extraction when
        resource allocation is deterministic.

        Returns OCCUPIED when:
        - Resources are explicitly present in document structure
        - Resource allocation to the block is deterministic

        Returns AMBIGUOUS when:
        - Multiple resources present but unclear which are allocated
        - Document structure provides alternatives rather than allocations

        Does NOT extract when:
        - No resources present (optional - not an error)
        - Resource identity unresolvable
        """
        result = OccupancyExtractionResult()

        # Resources are optional - no resources is not a blocker
        if len(block.resource_candidates) == 0:
            result.extraction_successful = True  # No extraction needed, not an error
            return result

        # Check for genuine resource allocation issues
        error_codes = {
            issue.code
            for issue in block.issues
            if issue.severity == ValidationSeverity.ERROR
        }

        # Case 1: Unresolvable resource identity
        if 'UNRESOLVED_RESOURCE_IDENTITY' in error_codes:
            result.extraction_successful = False
            result.blocked_reason = "UNRESOLVED_RESOURCE_IDENTITY: Cannot resolve resource code"
            return result

        # Case 2: Ambiguous resource for single activity
        if 'AMBIGUOUS_RESOURCE_SINGLE_ACTIVITY' in error_codes:
            # Multiple resources but unclear which is allocated
            result.extraction_successful = False
            result.blocked_reason = "AMBIGUOUS_RESOURCE_SINGLE_ACTIVITY: Unclear which resource allocated"
            return result

        # Case 3: Activity tokenization ambiguous BUT resources deterministic
        # Example: "PY1, PE2, DS 3,4" with (LAB1B) (LAB 1A)
        # Both labs are allocated to this block - mark both OCCUPIED

        if 'ACTIVITY_TOKENIZATION_AMBIGUOUS' in error_codes:
            extraction_reason = "Activity tokenization ambiguous but resource allocation deterministic"

            for resource_candidate in block.resource_candidates:
                occupancies = cls._create_resource_occupancies(
                    resource_candidate=resource_candidate,
                    day=block.day,
                    slots=block.slots,
                    status=OccupancyStatus.OCCUPIED,
                    source_location=block.source_location,
                    original_text=block.original_text,
                    extraction_reason=extraction_reason
                )
                result.resource_occupancies.extend(occupancies)

            result.extraction_successful = True
            return result

        # Case 4: Multiple resources allocated to block
        # If document structure shows multiple resources for the block,
        # all are allocated (e.g., multiple labs for multiple activities)
        if len(block.resource_candidates) > 1:
            extraction_reason = "Multiple resources allocated to block"

            for resource_candidate in block.resource_candidates:
                occupancies = cls._create_resource_occupancies(
                    resource_candidate=resource_candidate,
                    day=block.day,
                    slots=block.slots,
                    status=OccupancyStatus.OCCUPIED,
                    source_location=block.source_location,
                    original_text=block.original_text,
                    extraction_reason=extraction_reason
                )
                result.resource_occupancies.extend(occupancies)

            result.extraction_successful = True
            return result

        # Case 5: Standard single resource
        if len(block.resource_candidates) == 1:
            extraction_reason = "Resource allocation deterministic from document structure"

            resource_candidate = block.resource_candidates[0]
            occupancies = cls._create_resource_occupancies(
                resource_candidate=resource_candidate,
                day=block.day,
                slots=block.slots,
                status=OccupancyStatus.OCCUPIED,
                source_location=block.source_location,
                original_text=block.original_text,
                extraction_reason=extraction_reason
            )
            result.resource_occupancies.extend(occupancies)

            result.extraction_successful = True
            return result

        # Default: extraction successful (no resources is valid)
        result.extraction_successful = True
        return result

    @classmethod
    def _create_teacher_occupancies(
        cls,
        teacher_candidate: TeacherCandidate,
        day: str,
        slots: list[str],
        status: OccupancyStatus,
        source_location: SourceLocation,
        original_text: str,
        extraction_reason: str
    ) -> list[TeacherOccupancy]:
        """Create occupancy records for each working slot.

        IMPORTANT: Multi-slot blocks produce multiple occupancy records.
        Example: S4+S5 produces TWO records (one for S4, one for S5).

        Args:
            teacher_candidate: Teacher candidate
            day: Day name
            slots: List of slot codes (e.g., ["S4", "S5"])
            status: Occupancy status
            source_location: Source location
            original_text: Original cell text
            extraction_reason: Why occupancy extracted

        Returns:
            List of TeacherOccupancy records (one per working slot)
        """
        occupancies = []

        for slot in slots:
            # Skip break slots
            if slot in cls.BREAK_SLOTS:
                continue

            occupancies.append(TeacherOccupancy(
                teacher_acronym=teacher_candidate.normalized_acronym,
                day=day,
                slot=slot,
                status=status,
                source_location=source_location,
                source_block_original_text=original_text,
                extraction_reason=extraction_reason
            ))

        return occupancies

    @classmethod
    def _create_resource_occupancies(
        cls,
        resource_candidate: ResourceCandidate,
        day: str,
        slots: list[str],
        status: OccupancyStatus,
        source_location: SourceLocation,
        original_text: str,
        extraction_reason: str
    ) -> list[ResourceOccupancy]:
        """Create occupancy records for each working slot.

        IMPORTANT: Multi-slot blocks produce multiple occupancy records.
        Example: S4+S5 produces TWO records (one for S4, one for S5).

        Args:
            resource_candidate: Resource candidate
            day: Day name
            slots: List of slot codes
            status: Occupancy status
            source_location: Source location
            original_text: Original cell text
            extraction_reason: Why occupancy extracted

        Returns:
            List of ResourceOccupancy records (one per working slot)
        """
        occupancies = []

        for slot in slots:
            # Skip break slots
            if slot in cls.BREAK_SLOTS:
                continue

            occupancies.append(ResourceOccupancy(
                resource_code=resource_candidate.normalized_code,
                day=day,
                slot=slot,
                status=status,
                source_location=source_location,
                source_block_original_text=original_text,
                extraction_reason=extraction_reason
            ))

        return occupancies
