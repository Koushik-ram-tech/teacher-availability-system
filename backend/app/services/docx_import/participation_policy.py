"""Activity participation/ownership classification policy.

This module provides a CONFIGURABLE, institution-level policy layer that
classifies activities by who owns/manages them.

Architecture principle:
    The low-level DOCX parser extracts structural content only.
    It does NOT infer policy (e.g. "Placement = students").
    This module is the correct place for institution-specific policy.

Usage:
    from .participation_policy import classify_activity, ActivityParticipationPolicy

    policy = classify_activity("Placement")
    # → ActivityParticipationPolicy.STUDENT_MANAGED

    policy = classify_activity("DBMS")
    # → ActivityParticipationPolicy.FACULTY_MANAGED (default)

To add new institution-specific rules, extend DEFAULT_POLICY_MAP.
No parser changes are needed.
"""

from enum import Enum
from typing import Optional


class ActivityParticipationPolicy(str, Enum):
    """Who owns/manages an activity from a resource/teacher allocation perspective.

    STUDENT_MANAGED:
        The activity is conducted by students independently.
        No faculty teacher is required or assigned.
        Teacher absence is INTENTIONAL, not a data error.
        Examples: Placement, Cultural activity, Physical activity, VAC

    FACULTY_MANAGED:
        Normal faculty-led class or lab.
        Teacher presence is expected.
        Teacher absence is a data issue to be reviewed.
        Examples: DBMS, Python Lab, ADA2

    EXTERNAL:
        Activity managed by external persons (e.g. industry guests).
        Teacher may be Ind* or similar external token.

    UNKNOWN:
        Cannot be classified from the activity code alone.
        Treated as FACULTY_MANAGED for safety.
    """
    STUDENT_MANAGED = "STUDENT_MANAGED"
    FACULTY_MANAGED = "FACULTY_MANAGED"
    EXTERNAL = "EXTERNAL"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Default policy map
# ---------------------------------------------------------------------------
# Keys are NORMALIZED activity codes (uppercase, stripped).
# Values are ActivityParticipationPolicy.
#
# This is the ONLY place institution-specific activity policy is configured.
# The DOCX parser and OccupancyExtractor do NOT know about these rules.
#
# To add a new student-managed activity:
#   Add its normalized name as a key → ActivityParticipationPolicy.STUDENT_MANAGED
# ---------------------------------------------------------------------------

DEFAULT_POLICY_MAP: dict[str, ActivityParticipationPolicy] = {
    # Placement / industry / career events
    "PLACEMENT": ActivityParticipationPolicy.STUDENT_MANAGED,

    # Cultural / extracurricular
    "CULTURAL ACTIVITY": ActivityParticipationPolicy.STUDENT_MANAGED,
    "CULTURAL ACTIVITIES": ActivityParticipationPolicy.STUDENT_MANAGED,

    # Physical / sports
    "PHYSICAL ACTIVITY": ActivityParticipationPolicy.STUDENT_MANAGED,
    "PHYSICAL ACTIVITIES": ActivityParticipationPolicy.STUDENT_MANAGED,

    # Library / self-study
    "LIBRARY/RESEARCH ACTIVITY": ActivityParticipationPolicy.STUDENT_MANAGED,
    "LIBRARY / RESEARCH ACTIVITY": ActivityParticipationPolicy.STUDENT_MANAGED,
    "LIBRARY": ActivityParticipationPolicy.STUDENT_MANAGED,

    # Project work (self-managed)
    "MINI PROJECT": ActivityParticipationPolicy.STUDENT_MANAGED,
    "MINI PROJECT LAB": ActivityParticipationPolicy.STUDENT_MANAGED,

    # Vacation / break
    "VAC": ActivityParticipationPolicy.STUDENT_MANAGED,

    # Extended class (student-led)
    "EXTENDED CLASS": ActivityParticipationPolicy.STUDENT_MANAGED,
    "EXTENDED CLASSES": ActivityParticipationPolicy.STUDENT_MANAGED,
}


def normalize_activity_code(code: str) -> str:
    """Normalize an activity code for policy lookup.

    Strips whitespace and converts to uppercase.
    Does NOT remove punctuation to preserve codes like 'LIBRARY/RESEARCH ACTIVITY'.
    """
    return code.strip().upper()


def classify_activity(
    activity_code: str,
    policy_map: Optional[dict[str, ActivityParticipationPolicy]] = None,
) -> ActivityParticipationPolicy:
    """Classify an activity code according to the participation policy map.

    Args:
        activity_code: Activity code as extracted from the timetable cell.
        policy_map: Custom policy map (defaults to DEFAULT_POLICY_MAP).

    Returns:
        ActivityParticipationPolicy for the activity.
        Returns FACULTY_MANAGED if not found in policy map (safe default).

    Examples:
        classify_activity("Placement")    → STUDENT_MANAGED
        classify_activity("DBMS")         → FACULTY_MANAGED  (default)
        classify_activity("VAC")          → STUDENT_MANAGED
        classify_activity("ADA2 Lab")     → FACULTY_MANAGED  (default)
    """
    if policy_map is None:
        policy_map = DEFAULT_POLICY_MAP

    normalized = normalize_activity_code(activity_code)
    return policy_map.get(normalized, ActivityParticipationPolicy.FACULTY_MANAGED)


def is_student_managed(
    activity_code: str,
    policy_map: Optional[dict[str, ActivityParticipationPolicy]] = None,
) -> bool:
    """Convenience helper: return True if activity is student-managed.

    Args:
        activity_code: Activity code from timetable cell.
        policy_map: Custom policy map (defaults to DEFAULT_POLICY_MAP).

    Returns:
        True if STUDENT_MANAGED, False otherwise.
    """
    return classify_activity(activity_code, policy_map) == ActivityParticipationPolicy.STUDENT_MANAGED
