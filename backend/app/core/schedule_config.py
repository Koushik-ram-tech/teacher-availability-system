"""Fixed department schedule configuration.

This module defines the canonical time slots and breaks for the prototype.
S1–S9 are working slots (Monday–Saturday); breaks are fixed and not
schedulable. Day-of-week mapping uses ISO weekday integers (1=Monday, 7=Sunday).

DO NOT modify slot definitions here unless the database time_slots table has
been updated to match.  The backend resolves slot codes to UUIDs at runtime
by querying time_slots.code.
"""
from dataclasses import dataclass, field
from typing import Literal

# Days available for timetabling in this prototype.
# ISO weekday integers: 1=Monday … 6=Saturday.  7=Sunday is excluded.
WORKING_DAYS: list[int] = [1, 2, 3, 4, 5, 6]

DAY_NAME_TO_ISO: dict[str, int] = {
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
}

ISO_TO_DAY_NAME: dict[int, str] = {v: k for k, v in DAY_NAME_TO_ISO.items()}

VALID_DAY_NAMES: frozenset[str] = frozenset(DAY_NAME_TO_ISO.keys())


@dataclass(frozen=True)
class PeriodConfig:
    kind: Literal["SLOT", "BREAK"]
    code: str | None  # None for BREAK rows
    label: str | None  # Human-readable label, None for regular SLOT rows
    start_time: str   # "HH:MM:SS"
    end_time: str     # "HH:MM:SS"


# Canonical period sequence, ordered by start time.
# This is the source-of-truth for building GET responses when no timetable
# entry has been saved for a particular slot.
PERIOD_SEQUENCE: list[PeriodConfig] = [
    PeriodConfig("SLOT",  "S1", None,           "08:00:00", "08:55:00"),
    PeriodConfig("SLOT",  "S2", None,           "08:55:00", "09:50:00"),
    PeriodConfig("SLOT",  "S3", None,           "09:50:00", "10:45:00"),
    PeriodConfig("BREAK", None, "Morning break","10:45:00", "11:15:00"),
    PeriodConfig("SLOT",  "S4", None,           "11:15:00", "12:10:00"),
    PeriodConfig("SLOT",  "S5", None,           "12:10:00", "13:05:00"),
    PeriodConfig("BREAK", None, "Lunch",        "13:05:00", "14:00:00"),
    PeriodConfig("SLOT",  "S6", None,           "14:00:00", "14:55:00"),
    PeriodConfig("SLOT",  "S7", None,           "14:55:00", "15:50:00"),
    PeriodConfig("SLOT",  "S8", None,           "15:50:00", "16:45:00"),
    PeriodConfig("SLOT",  "S9", None,           "16:45:00", "17:40:00"),
]

# Ordered list of slot codes only (no breaks).
SLOT_CODES: list[str] = [p.code for p in PERIOD_SEQUENCE if p.kind == "SLOT" and p.code]

# ---------------------------------------------------------------------------
# Lab consecutive-slot pairs
# A LAB must span exactly two slots that are adjacent in the period sequence
# with no break between them.  S3→S4 and S5→S6 are NOT valid because the
# morning break (10:45–11:15) and lunch break (13:05–14:00) separate them.
#
# Stored as frozensets so pair membership can be tested regardless of order:
#   {S1, S2}, {S2, S3}, {S4, S5}, {S6, S7}, {S7, S8}, {S8, S9}
# ---------------------------------------------------------------------------

# Build consecutive pairs by walking the period sequence and collecting
# every (SLOT, SLOT) pair with no BREAK between them.
_slot_sequence: list[str] = []
VALID_LAB_PAIRS: frozenset[frozenset[str]] = frozenset()
_adjacent_pairs: list[frozenset[str]] = []
_prev_was_slot: bool = False
_prev_code: str | None = None
for _period in PERIOD_SEQUENCE:
    if _period.kind == "BREAK":
        _prev_was_slot = False
        _prev_code = None
    elif _period.code is not None:
        if _prev_was_slot and _prev_code is not None:
            _adjacent_pairs.append(frozenset({_prev_code, _period.code}))
        _prev_was_slot = True
        _prev_code = _period.code

VALID_LAB_PAIRS = frozenset(_adjacent_pairs)

# ---------------------------------------------------------------------------
# Academic year format
# Required format: YYYY-YYYY where the second year is exactly first + 1.
# e.g. 2025-2026 is valid; 2025-2027 or 2025 are not.
# ---------------------------------------------------------------------------
import re as _re  # noqa: E402  (import at bottom to keep dataclass section clean)

ACADEMIC_YEAR_PATTERN = _re.compile(r"^\d{4}-\d{4}$")


def is_valid_academic_year(value: str) -> bool:
    """Return True if *value* matches YYYY-YYYY with consecutive years."""
    value = value.strip()
    if not ACADEMIC_YEAR_PATTERN.match(value):
        return False
    start_str, end_str = value.split("-")
    return int(end_str) == int(start_str) + 1
