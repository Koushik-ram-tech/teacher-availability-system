"""Resolution rules for determining if TimetableBlock is RESOLVED.

Implements the authoritative resolution criteria from specification.
"""

from typing import Optional

from .staging import TimetableBlock
from app.domain.timetable import ValidationIssue, ValidationSeverity


class ResolutionRule:
    """AUTHORITATIVE RULE for determining if TimetableBlock's Activity is RESOLVED.

    An Activity is RESOLVED if and only if ALL of the following are true:
    1. EXACTLY ONE ActivityCandidate exists
    2. The ActivityCandidate is NOT marked as tokenization-ambiguous

    Note: Teacher and Resource allocations are handled independently by OccupancyExtractor.
    """

    @staticmethod
    def classify_block(block: TimetableBlock) -> None:
        """Check semantic resolution criteria and identify identity issues.

        Modifies block in-place:
        - Sets activity_semantic_status
        - Adds ERROR-level issues for identity resolution failures
        """
        # Rule 1: Exactly one activity
        if len(block.activity_candidates) == 0:
            block.activity_semantic_status = "MISSING"
            block.issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="MISSING_SUBJECT",
                message="Activity cell has no subject/activity",
                affected_entities={"type": "activity"},
                source_locations=[block.source_location]
            ))
        elif len(block.activity_candidates) > 1:
            block.activity_semantic_status = "AMBIGUOUS"
            block.issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="AMBIGUOUS_BLOCK",
                message=f"Cell contains {len(block.activity_candidates)} activities.",
                affected_entities={"type": "activity", "count": str(len(block.activity_candidates))},
                source_locations=[block.source_location]
            ))
        else:
            activity = block.activity_candidates[0]
            if activity.is_tokenization_ambiguous:
                # Semantically ambiguous (e.g. "PE 1,2,3,4" — unclear boundaries).
                # NOTE: This does NOT block occupancy extraction.
                # OccupancyExtractor runs independently and ignores activity_semantic_status.
                block.activity_semantic_status = "AMBIGUOUS"
            else:
                block.activity_semantic_status = "RESOLVED"

        # Check Teacher Identities (Required by OccupancyExtractor)
        for teacher in block.teacher_candidates:
            if not teacher.is_identity_resolvable:
                block.issues.append(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="UNRESOLVED_TEACHER_IDENTITY",
                    message=f"Teacher '{teacher.acronym}' cannot be resolved to unique identity",
                    affected_entities={"type": "teacher", "acronym": teacher.acronym},
                    source_locations=[block.source_location]
                ))

        # Check Resource Identities (Required by OccupancyExtractor)
        for resource in block.resource_candidates:
            if not resource.is_identity_resolvable:
                block.issues.append(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="UNRESOLVED_RESOURCE_IDENTITY",
                    message=f"Resource '{resource.code}' cannot be resolved to unique identity",
                    affected_entities={"type": "resource", "code": resource.code},
                    source_locations=[block.source_location]
                ))
