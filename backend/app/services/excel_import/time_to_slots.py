"""
excel_import.time_to_slots
--------------------------
Converts human-readable time range strings from the college-facing Excel
workbook into internal slot codes (S1–S9).

The mapping is fixed and institutional — it matches exactly the slot
boundaries defined in ``schedule_config.PERIOD_SEQUENCE``.

Only exact boundary matches are accepted.  Arbitrary times are rejected
so that typos in the college workbook surface as clear validation errors
rather than silent mismatches.

Supported input formats (case-insensitive, flexible whitespace):
  "8:00 AM - 8:55 AM"      → ["S1"]
  "8:00 AM - 9:50 AM"      → error  (not a recognised single or double span)
  "2:00 PM - 3:50 PM"      → ["S6", "S7"]   (LAB double-slot)
  "2:00 PM – 3:50 PM"      → ["S6", "S7"]   (en-dash also accepted)

A single slot span maps to exactly one code.
A LAB double-slot span maps to exactly two consecutive codes.
Any other span is rejected.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Slot boundary table — must stay in sync with schedule_config.PERIOD_SEQUENCE
# ---------------------------------------------------------------------------
# Each entry: (start_minutes_from_midnight, end_minutes_from_midnight, slot_code)

def _hhmm(h: int, m: int) -> int:
    return h * 60 + m


_SLOT_BOUNDARIES: list[tuple[int, int, str]] = [
    (_hhmm(8,  0),  _hhmm(8,  55), "S1"),
    (_hhmm(8,  55), _hhmm(9,  50), "S2"),
    (_hhmm(9,  50), _hhmm(10, 45), "S3"),
    (_hhmm(11, 15), _hhmm(12, 10), "S4"),
    (_hhmm(12, 10), _hhmm(13,  5), "S5"),
    (_hhmm(14,  0), _hhmm(14, 55), "S6"),
    (_hhmm(14, 55), _hhmm(15, 50), "S7"),
    (_hhmm(15, 50), _hhmm(16, 45), "S8"),
    (_hhmm(16, 45), _hhmm(17, 40), "S9"),
]

# Index: start_minutes → (end_minutes, slot_code)
_START_TO_SLOT: dict[int, tuple[int, str]] = {
    start: (end, code) for start, end, code in _SLOT_BOUNDARIES
}
# Index: end_minutes → slot_code (used when matching the end of a double span)
_END_TO_CODE: dict[int, str] = {end: code for _, end, code in _SLOT_BOUNDARIES}

# ---------------------------------------------------------------------------
# Time string parser
# ---------------------------------------------------------------------------

# Matches "8:00 AM", "12:10 PM", etc.  Groups: (hour, minute, ampm)
_TIME_PATTERN = re.compile(
    r"(\d{1,2}):(\d{2})\s*(AM|PM)",
    re.IGNORECASE,
)

# Separator: " - " or " – " (en-dash) with optional surrounding whitespace
_SEP_PATTERN = re.compile(r"\s*[-–]\s*")


class TimeConversionError(Exception):
    """Raised when a time range string cannot be converted to slot codes."""


def _parse_time_str(raw: str) -> int:
    """Parse a time string like '8:00 AM' into minutes-from-midnight."""
    m = _TIME_PATTERN.fullmatch(raw.strip())
    if not m:
        raise TimeConversionError(
            f"Cannot parse time '{raw}'. Expected format: 'H:MM AM' or 'H:MM PM'."
        )
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if ampm == "PM" and hour != 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    return _hhmm(hour, minute)


def time_range_to_slots(time_range: str) -> list[str]:
    """
    Convert a human-readable time range to a list of internal slot codes.

    Examples::

        time_range_to_slots("8:00 AM - 8:55 AM")   → ["S1"]
        time_range_to_slots("2:00 PM - 3:50 PM")   → ["S6", "S7"]
        time_range_to_slots("2:00 PM – 3:50 PM")   → ["S6", "S7"]  (en-dash)

    Raises :class:`TimeConversionError` for any unrecognised range.
    """
    # Split on the separator dash/en-dash
    parts = _SEP_PATTERN.split(time_range.strip(), maxsplit=1)
    if len(parts) != 2:
        raise TimeConversionError(
            f"Time range '{time_range}' does not contain a recognisable separator "
            "(' - ' or ' – '). Expected format: 'H:MM AM - H:MM PM'."
        )

    try:
        start_min = _parse_time_str(parts[0])
        end_min = _parse_time_str(parts[1])
    except TimeConversionError:
        raise  # propagate with original message

    # Look up the slot that begins at start_min
    if start_min not in _START_TO_SLOT:
        raise TimeConversionError(
            f"Time range '{time_range}': start time does not match any "
            "institutional slot boundary. "
            f"Valid start times: {_valid_starts_str()}."
        )

    slot_end, first_code = _START_TO_SLOT[start_min]

    if end_min == slot_end:
        # Exact single-slot match
        return [first_code]

    # Check whether end_min matches the end of the *next* slot (double span)
    if end_min in _END_TO_CODE:
        second_code = _END_TO_CODE[end_min]
        # Verify the two slots are actually consecutive (valid LAB pair)
        from app.core.schedule_config import VALID_LAB_PAIRS
        pair = frozenset({first_code, second_code})
        if pair in VALID_LAB_PAIRS:
            # Return them sorted by sequence
            from app.core.schedule_config import SLOT_CODES
            return sorted([first_code, second_code], key=lambda c: SLOT_CODES.index(c))
        raise TimeConversionError(
            f"Time range '{time_range}' spans {first_code}+{second_code}, "
            "which are not a valid consecutive LAB pair "
            "(a break falls between them)."
        )

    raise TimeConversionError(
        f"Time range '{time_range}': end time does not match any "
        "institutional slot boundary or recognised double-slot span. "
        f"Valid end times: {_valid_ends_str()}."
    )


def _valid_starts_str() -> str:
    times = []
    for start, _, code in _SLOT_BOUNDARIES:
        h, m = divmod(start, 60)
        ampm = "AM" if h < 12 else "PM"
        h12 = h if h <= 12 else h - 12
        if h12 == 0:
            h12 = 12
        times.append(f"{h12}:{m:02d} {ampm} ({code})")
    return ", ".join(times)


def _valid_ends_str() -> str:
    times = []
    for _, end, code in _SLOT_BOUNDARIES:
        h, m = divmod(end, 60)
        ampm = "AM" if h < 12 else "PM"
        h12 = h if h <= 12 else h - 12
        if h12 == 0:
            h12 = 12
        times.append(f"{h12}:{m:02d} {ampm} ({code})")
    return ", ".join(times)
