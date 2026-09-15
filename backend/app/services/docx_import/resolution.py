"""Resolution rules for determining if TimetableBlock is RESOLVED.

Implements the authoritative resolution criteria from specification.
"""

from typing import Optional

from .staging import TimetableBlock
from app.domain.timetable import ValidationIssue, ValidationSeverity


class ResolutionRule:
    """AUTHORITATIVE RULE for determining if TimetableBlock is RESOLVED.

    A block is RESOLVED if and only if ALL of the following are true:

    1. EXACTLY ONE ActivityCandidate exists
    2. The ActivityCandidate is NOT marked as tokenization-ambiguous
    3. EXACTLY ONE TeacherCandidate exists
    4. The TeacherCandidate identity is uniquely resolvable
    5. ZERO or ONE ResourceCandidate exists
    6. If ResourceCandidate exists, its identity is uniquely resolvable
    7. There are NO ERROR-level ValidationIssues
    8. There is NO unresolved activity-tokenization ambiguity
    9. There is NO unresolved relationship ambiguity

    Candidate count alone NEVER implies semantic resolution.
    """

    @staticmethod
    def classify_block(block: TimetableBlock) -> tuple[bool, Optional[str]]:
        """Check if block meets resolution criteria.

        Modifies block in-place:
        - Sets is_resolved
        - Sets ambiguity_reason
        - Adds ERROR-level issues if unresolved

        Returns:
            (True, None) if resolved
            (False, reason) if unresolved
        """
        # Rule 1: Exactly one activity
        if len(block.activity_candidates) == 0:
            reason = "No activity candidates"
            block.is_resolved = False
            block.ambiguity_reason = reason
            block.issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="MISSING_SUBJECT",
                message="Activity cell has no subject/activity",
                affected_entities={"type": "activity"},
                source_locations=[block.source_location]
            ))
            return False, reason

        if len(block.activity_candidates) > 1:
            reason = f"{len(block.activity_candidates)} activity candidates (need exactly 1)"
            block.is_resolved = False
            block.ambiguity_reason = reason
            block.issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="AMBIGUOUS_BLOCK",
                message=f"Cell contains {len(block.activity_candidates)} activities with {len(block.teacher_candidates)} teachers. Cannot determine which teacher teaches which activity.",
                affected_entities={"type": "activity", "count": str(len(block.activity_candidates))},
                source_locations=[block.source_location]
            ))
            return False, reason

        activity = block.activity_candidates[0]

        # Rule 2: Activity not tokenization-ambiguous
        if activity.is_tokenization_ambiguous:
            reason = f"Activity '{activity.code}' has ambiguous boundaries"
            block.is_resolved = False
            block.ambiguity_reason = reason
            block.issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="ACTIVITY_TOKENIZATION_AMBIGUOUS",
                message=f"Activity phrase '{activity.code}' contains punctuation that may indicate multiple activities, but structure doesn't provide definitive boundaries.",
                affected_entities={"type": "activity", "code": activity.code},
                source_locations=[block.source_location]
            ))
            return False, reason

        # Rule 3: Exactly one teacher
        if len(block.teacher_candidates) == 0:
            reason = "No teacher candidates"
            block.is_resolved = False
            block.ambiguity_reason = reason
            block.issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="MISSING_TEACHER",
                message=f"Activity '{activity.code}' has no teacher specified. Teacher required for availability system.",
                affected_entities={"type": "teacher", "activity": activity.code},
                source_locations=[block.source_location]
            ))
            return False, reason

        if len(block.teacher_candidates) > 1:
            reason = f"{len(block.teacher_candidates)} teacher candidates (need exactly 1)"
            block.is_resolved = False
            block.ambiguity_reason = reason
            block.issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="AMBIGUOUS_TEACHER_MAPPING",
                message=f"Cell contains {len(block.teacher_candidates)} teachers for activity '{activity.code}'. Cannot determine correct teacher assignment.",
                affected_entities={"type": "teacher", "count": str(len(block.teacher_candidates)), "activity": activity.code},
                source_locations=[block.source_location]
            ))
            return False, reason

        teacher = block.teacher_candidates[0]

        # Rule 4: Teacher identity resolvable
        if not teacher.is_identity_resolvable:
            reason = f"Teacher '{teacher.acronym}' identity not uniquely resolvable"
            block.is_resolved = False
            block.ambiguity_reason = reason
            block.issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="UNRESOLVED_TEACHER_IDENTITY",
                message=f"Teacher '{teacher.acronym}' cannot be resolved to unique identity",
                affected_entities={"type": "teacher", "acronym": teacher.acronym},
                source_locations=[block.source_location]
            ))
            return False, reason

        # Rule 5: Zero or one resource
        if len(block.resource_candidates) > 1:
            reason = f"{len(block.resource_candidates)} resource candidates (need 0 or 1)"
            block.is_resolved = False
            block.ambiguity_reason = reason
            block.issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="AMBIGUOUS_RESOURCE_SINGLE_ACTIVITY",
                message=f"Activity '{activity.code}' has {len(block.resource_candidates)} possible resources. Manual resolution required.",
                affected_entities={"type": "resource", "count": str(len(block.resource_candidates)), "activity": activity.code},
                source_locations=[block.source_location]
            ))
            return False, reason

        # Rule 6: If resource exists, identity resolvable
        if len(block.resource_candidates) == 1:
            resource = block.resource_candidates[0]
            if not resource.is_identity_resolvable:
                reason = f"Resource '{resource.code}' identity not uniquely resolvable"
                block.is_resolved = False
                block.ambiguity_reason = reason
                block.issues.append(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="UNRESOLVED_RESOURCE_IDENTITY",
                    message=f"Resource '{resource.code}' cannot be resolved to unique identity",
                    affected_entities={"type": "resource", "code": resource.code},
                    source_locations=[block.source_location]
                ))
                return False, reason

        # Rule 7: No ERROR-level issues (check existing issues)
        errors = [issue for issue in block.issues if issue.severity == ValidationSeverity.ERROR]
        if errors:
            reason = f"{len(errors)} ERROR-level validation issue(s)"
            block.is_resolved = False
            block.ambiguity_reason = reason
            return False, reason

        # All rules passed
        block.is_resolved = True
        block.ambiguity_reason = None
        return True, None
