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

    # EXTERNAL flag: True for industry/guest participants (Ind*, etc.)
    # External participants occupy the slot but do NOT create Teacher DB rows.
    # Their presence is preserved in ScheduleActivity.notes.
    is_external: bool = False

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

        IMPORTANT: Subject/activity ambiguity does NOT block extraction.
        All structurally associated teachers are marked OCCUPIED.

        Does NOT extract when:
        - Teacher identity unresolvable (returns extraction failed)
        """
        result = OccupancyExtractionResult()

        error_codes = {
            issue.code
            for issue in block.issues
            if issue.severity == ValidationSeverity.ERROR
        }

        # Case 1: No teachers present
        if len(block.teacher_candidates) == 0:
            block.teacher_occupancy_status = "UNSPECIFIED"
            result.extraction_successful = True
            return result

        # Case 2: Unresolvable teacher identity
        if 'UNRESOLVED_TEACHER_IDENTITY' in error_codes:
            result.extraction_successful = False
            result.blocked_reason = "UNRESOLVED_TEACHER_IDENTITY: Cannot resolve teacher acronym"
            return result

        # Case 3: All extracted teachers allocated to this block.
        # FACULTY and NAME_ONLY → standard faculty occupancy records.
        # EXTERNAL (e.g. Ind*) → occupancy records with is_external=True;
        #   these mark the slot as occupied but do NOT create Teacher DB rows.
        faculty_candidates = [
            tc for tc in block.teacher_candidates
            if tc.role in ("FACULTY",)  # OccupantRole.FACULTY
        ]
        # NAME_ONLY counts as faculty for availability
        name_only_candidates = [
            tc for tc in block.teacher_candidates
            if tc.is_name_only
        ]
        external_candidates = [
            tc for tc in block.teacher_candidates
            if tc.role == "EXTERNAL" and not tc.is_name_only  # OccupantRole.EXTERNAL
        ]
        occupied_candidates = faculty_candidates + [
            tc for tc in name_only_candidates if tc not in faculty_candidates
        ]

        if not occupied_candidates and not external_candidates:
            # All teachers unrecognised (should be caught by identity check above)
            block.teacher_occupancy_status = "UNSPECIFIED"
            result.extraction_successful = True
            return result

        if occupied_candidates or external_candidates:
            extraction_reason_faculty = "Teacher allocation deterministic from document structure"
            extraction_reason_external = "External participant occupancy (Ind* / industry person)"
            block.teacher_occupancy_status = "DETERMINISTIC"

            for teacher_candidate in occupied_candidates:
                occupancies = cls._create_teacher_occupancies(
                    teacher_candidate=teacher_candidate,
                    day=block.day,
                    slots=block.slots,
                    status=OccupancyStatus.OCCUPIED,
                    source_location=block.source_location,
                    original_text=block.original_text,
                    extraction_reason=extraction_reason_faculty,
                    is_external=False,
                )
                result.teacher_occupancies.extend(occupancies)

            for ext_candidate in external_candidates:
                occupancies = cls._create_teacher_occupancies(
                    teacher_candidate=ext_candidate,
                    day=block.day,
                    slots=block.slots,
                    status=OccupancyStatus.OCCUPIED,
                    source_location=block.source_location,
                    original_text=block.original_text,
                    extraction_reason=extraction_reason_external,
                    is_external=True,
                )
                result.teacher_occupancies.extend(occupancies)

        result.extraction_successful = True
        return result

    @classmethod
    def _extract_resource_occupancy(cls, block: TimetableBlock) -> OccupancyExtractionResult:
        """Extract resource occupancy from block.

        IMPORTANT: Subject/activity ambiguity does NOT block extraction.
        """
        result = OccupancyExtractionResult()

        # Resources are optional
        if len(block.resource_candidates) == 0:
            block.resource_occupancy_status = "UNSPECIFIED"
            result.extraction_successful = True
            return result

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

        # Case 2: Multiple resources
        # Distinguish comma-list (all OCCUPIED) from slash-list (AMBIGUOUS)
        if len(block.resource_candidates) > 1:
            # Check if all are comma-separated (deterministic occupancy)
            # vs slash-separated (genuine alternative)
            all_deterministic = all(
                not rc.is_assignment_ambiguous for rc in block.resource_candidates
            )
            any_ambiguous = any(
                rc.is_assignment_ambiguous for rc in block.resource_candidates
            )

            if any_ambiguous:
                # Slash-separated alternatives: genuine uncertainty
                extraction_reason = "Slash-separated resource alternatives — choice unclear"
                block.resource_occupancy_status = "AMBIGUOUS"
                status = OccupancyStatus.AMBIGUOUS
            else:
                # Comma-separated list: all resources deterministically occupied
                # (e.g. (CA3, CA2) -> both rooms used for parallel groups)
                extraction_reason = "Comma-separated resource list — all resources occupied"
                block.resource_occupancy_status = "DETERMINISTIC"
                status = OccupancyStatus.OCCUPIED

            for resource_candidate in block.resource_candidates:
                occupancies = cls._create_resource_occupancies(
                    resource_candidate=resource_candidate,
                    day=block.day,
                    slots=block.slots,
                    status=status,
                    source_location=block.source_location,
                    original_text=block.original_text,
                    extraction_reason=extraction_reason,
                )
                result.resource_occupancies.extend(occupancies)

            result.extraction_successful = True
            return result

        # Case 3: Standard single resource
        extraction_reason = "Resource allocation deterministic from document structure"
        block.resource_occupancy_status = "DETERMINISTIC"

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

    @classmethod
    def _create_teacher_occupancies(
        cls,
        teacher_candidate: TeacherCandidate,
        day: str,
        slots: list[str],
        status: OccupancyStatus,
        source_location: SourceLocation,
        original_text: str,
        extraction_reason: str,
        is_external: bool = False,
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
            is_external: True for industry/guest participants (Ind*, etc.)

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
                extraction_reason=extraction_reason,
                is_external=is_external,
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
