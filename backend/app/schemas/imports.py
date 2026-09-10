"""Pydantic schemas for the Excel import workflow.

These schemas describe the data flowing between:
  POST /imports/excel      → ImportPreview
  POST /imports/{id}/confirm → ImportConfirmOut
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Teacher row (from Teachers sheet, after normalization + DB resolution)
# ---------------------------------------------------------------------------


class TeacherImportRow(BaseModel):
    """One row from the Teachers sheet after parsing and DB resolution."""

    row_ref: str  # e.g. "Teachers!2"
    action: Literal["CREATE", "REUSE", "CONFLICT"]
    name: str
    acronym: str
    level: str
    program_name: str
    semester: int
    department: str
    resolved_teacher_id: UUID | None = None
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Schedule entry row (from Schedule sheet, after normalization)
# ---------------------------------------------------------------------------


class ScheduleImportRow(BaseModel):
    """One row from the Schedule sheet after normalization."""

    row_refs: list[str]  # e.g. ["Schedule!3"]
    teacher_acronym: str
    day: str
    slot_ids: list[str]
    entry_type: str
    subject_or_activity: str | None = None
    section: str | None = None
    room: str | None = None
    notes: str | None = None
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Full preview returned by POST /imports/excel
# ---------------------------------------------------------------------------


class ImportPreview(BaseModel):
    """Normalized preview returned after parsing.  Errors prevent confirmation."""

    import_id: str
    academic_year: str
    teachers: list[TeacherImportRow] = []
    # Each key is a lowercase day name; entries span all teachers for that day.
    days: dict[str, list[ScheduleImportRow]] = {}
    warnings: list[str] = []
    errors: list[str] = []


# ---------------------------------------------------------------------------
# Confirm response
# ---------------------------------------------------------------------------


class ImportConfirmOut(BaseModel):
    """Response from a successful POST /imports/{import_id}/confirm."""

    import_id: str
    academic_year: str
    teachers_created: list[str] = []   # UUID strings
    teachers_reused: list[str] = []    # UUID strings
    timetables_created: list[str] = [] # UUID strings
    timetables_replaced: list[str] = [] # UUID strings
