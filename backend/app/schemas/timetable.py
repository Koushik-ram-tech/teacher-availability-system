"""Pydantic schemas for timetable read/write operations."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.schedule_config import SLOT_CODES, VALID_DAY_NAMES, VALID_LAB_PAIRS, is_valid_academic_year

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

TimetableStatusLiteral = Literal["DRAFT", "CONFIRMED"]
EntryTypeLiteral = Literal["CLASS", "LAB", "OTHER"]
SourceLiteral = Literal["MANUAL", "IMPORT"]


# ---------------------------------------------------------------------------
# Read schemas
# ---------------------------------------------------------------------------


class DayPeriodOut(BaseModel):
    """One row in the GET timetable response.

    kind='SLOT' rows carry a slot code and may carry an entry.
    kind='BREAK' rows are fixed; they never carry an entry.
    """

    kind: Literal["SLOT", "BREAK"]
    code: str | None = None
    label: str | None = None
    start_time: str
    end_time: str
    entry: "ScheduleEntryOut | None" = None


class ScheduleEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entry_type: EntryTypeLiteral
    subject_or_activity: str | None = None
    section: str | None = None
    room: str | None = None
    notes: str | None = None
    # Slot codes, not UUIDs.  The frontend works entirely in codes.
    slot_codes: list[str]


class TimetableDayOut(BaseModel):
    """Response shape for GET /teachers/{teacher_id}/timetable?day=&academic_year=&status="""

    teacher_id: UUID
    academic_year: str
    day: str
    timetable_status: TimetableStatusLiteral
    periods: list[DayPeriodOut]


# ---------------------------------------------------------------------------
# Write schemas
# ---------------------------------------------------------------------------


class ScheduleEntryIn(BaseModel):
    """One logical activity within a single day.

    subject_or_activity is optional.  The primary purpose of a timetable
    entry is occupancy/availability tracking: a teacher must be able to
    mark a slot as CLASS, LAB, or OTHER without naming a subject.
    When omitted, the API layer stores the entry_type string in the DB
    column (which has a NOT NULL + btrim <> '' constraint) so that the
    database constraint is satisfied without requiring schema changes.
    """

    slot_ids: list[str] = Field(min_length=1, description="Slot codes such as ['S1'] or ['S6', 'S7'].")
    entry_type: EntryTypeLiteral
    subject_or_activity: str | None = Field(default=None, max_length=500)
    section: str | None = Field(default=None, max_length=100)
    room: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("slot_ids")
    @classmethod
    def validate_slot_ids(cls, value: list[str]) -> list[str]:
        unknown = set(value) - set(SLOT_CODES)
        if unknown:
            raise ValueError(f"Unknown slot codes: {sorted(unknown)}.  Valid codes: {SLOT_CODES}")
        if len(value) != len(set(value)):
            raise ValueError("Duplicate slot codes within a single entry are not allowed.")
        return value

    @field_validator("entry_type")
    @classmethod
    def validate_entry_type(cls, value: str) -> str:
        # Stored for use in the cross-field LAB validator below.
        return value

    @model_validator(mode="after")
    def validate_lab_slots(self) -> "ScheduleEntryIn":
        """A LAB must occupy exactly two consecutive working slots.

        Consecutive means adjacent in the period sequence with no break
        between them.  Valid pairs: S1+S2, S2+S3, S4+S5, S6+S7, S7+S8,
        S8+S9.  Pairs crossing a break (S3+S4, S5+S6) are rejected.
        CLASS and OTHER entries do not inherit this constraint.
        """
        if self.entry_type != "LAB":
            return self
        pair = frozenset(self.slot_ids)
        if len(self.slot_ids) != 2 or pair not in VALID_LAB_PAIRS:
            valid = ", ".join(
                f"{sorted(p)[0]}+{sorted(p)[1]}" for p in sorted(VALID_LAB_PAIRS, key=sorted)
            )
            raise ValueError(
                f"A LAB must occupy exactly two consecutive working slots "
                f"(no break between them). Valid pairs: {valid}."
            )
        return self

    @property
    def effective_subject(self) -> str:
        """Return the value to persist in the DB column.

        The DB schema has ``subject_or_activity TEXT NOT NULL`` with a
        ``CHECK (btrim(subject_or_activity) <> '')`` constraint.  When the
        caller did not provide a subject, we store the entry_type string as
        a sentinel fallback (e.g. 'CLASS', 'LAB', 'OTHER').  Read-side code
        and the frontend's ``entryLabel()`` helper recognise this pattern
        and display it cleanly.
        """
        if not self.subject_or_activity or not self.subject_or_activity.strip():
            return self.entry_type
        return self.subject_or_activity.strip()


class TimetableWriteIn(BaseModel):
    """Payload for POST and PUT /teachers/{teacher_id}/timetable.

    academic_year is always required – the server never infers a default.
    days contains each weekday's list of entries (empty list = no entries).
    """

    academic_year: str = Field(min_length=1, max_length=20)
    days: dict[str, list[ScheduleEntryIn]] = Field(
        default_factory=dict,
        description="Keys are lowercase day names (monday–saturday). Missing keys are treated as empty.",
    )

    @field_validator("academic_year")
    @classmethod
    def strip_academic_year(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("academic_year cannot be blank.")
        if not is_valid_academic_year(value):
            raise ValueError(
                "academic_year must be in YYYY-YYYY format where the second year is "
                "one after the first (e.g. 2025-2026)."
            )
        return value

    @field_validator("days")
    @classmethod
    def validate_day_keys(cls, value: dict[str, list[ScheduleEntryIn]]) -> dict[str, list[ScheduleEntryIn]]:
        unknown = set(value.keys()) - VALID_DAY_NAMES
        if unknown:
            raise ValueError(f"Unknown day names: {sorted(unknown)}.  Valid names: {sorted(VALID_DAY_NAMES)}")
        return value

    @model_validator(mode="after")
    def no_duplicate_slots_per_day(self) -> "TimetableWriteIn":
        """Reject a payload where two entries on the same day claim the same slot code."""
        for day_name, entries in self.days.items():
            seen: set[str] = set()
            for entry in entries:
                overlap = seen & set(entry.slot_ids)
                if overlap:
                    raise ValueError(
                        f"Day '{day_name}': slot(s) {sorted(overlap)} are claimed by more than one entry."
                    )
                seen.update(entry.slot_ids)
        return self


# ---------------------------------------------------------------------------
# Write response schema
# ---------------------------------------------------------------------------


class TimetableWriteOut(BaseModel):
    """Minimal response returned after a successful POST or PUT."""

    id: UUID
    teacher_id: UUID
    academic_year: str
    status: TimetableStatusLiteral
    source: SourceLiteral
    days: dict[str, list[ScheduleEntryOut]]


# ---------------------------------------------------------------------------
# Timetable confirmation response
# ---------------------------------------------------------------------------


class TimetableConfirmOut(BaseModel):
    """Response returned after DRAFT → CONFIRMED promotion."""

    id: UUID
    teacher_id: UUID
    academic_year: str
    status: TimetableStatusLiteral     # always 'CONFIRMED' on success
    source: SourceLiteral
    confirmed_at: str                  # ISO-8601 timestamp (last_verified_at)
    entry_count: int                   # total schedule entries in the timetable
