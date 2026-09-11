"""
Canonical domain model for timetable import and management.

This module defines the source-agnostic representation of a teacher's
timetable that can be produced by XLSX import, DOCX import, or direct
editing, and consumed by validation and persistence.

Key design principles:
- SourceLocation supports DOCX/XLSX/MANUAL traceability
- ValidationIssue provides structured error/warning/info feedback
- TeacherIdentity is stable within a CanonicalTimetable to avoid duplication
- ScheduleActivity represents ONE logical activity (may span multiple slots)
- ActivitySlotRange represents the day and slot occupancy for one activity
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.domain.resources import ResourceReference


# ---------------------------------------------------------------------------
# Source Location
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceLocation:
    """
    Immutable reference to the source location of data.
    
    Supports XLSX, DOCX, and MANUAL sources.
    """
    
    source_type: Literal["XLSX", "DOCX", "MANUAL"]
    
    # XLSX fields
    sheet_name: str | None = None
    row_number: int | None = None
    field_name: str | None = None
    
    # DOCX fields
    table_index: int | None = None
    table_row: int | None = None
    table_col: int | None = None
    
    # Optional human-readable context
    display_context: str | None = None
    
    def to_display(self) -> str:
        """Generate human-readable reference."""
        if self.source_type == "XLSX":
            parts = []
            if self.sheet_name:
                parts.append(f"sheet '{self.sheet_name}'")
            if self.row_number is not None:
                parts.append(f"row {self.row_number}")
            if self.field_name:
                parts.append(f"field '{self.field_name}'")
            if parts:
                return f"XLSX: {', '.join(parts)}"
            return "XLSX source"
        
        elif self.source_type == "DOCX":
            parts = []
            if self.table_index is not None:
                parts.append(f"table {self.table_index}")
            if self.table_row is not None:
                parts.append(f"row {self.table_row}")
            if self.table_col is not None:
                parts.append(f"col {self.table_col}")
            if parts:
                return f"DOCX: {', '.join(parts)}"
            return "DOCX source"
        
        elif self.source_type == "MANUAL":
            return self.display_context or "Manual entry"
        
        return "Unknown source"


# ---------------------------------------------------------------------------
# Validation Issue
# ---------------------------------------------------------------------------


class ValidationSeverity(str, Enum):
    """Validation issue severity levels."""
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ValidationIssue:
    """
    Structured validation feedback with severity, code, message, and context.
    
    severity: ERROR blocks confirmation, WARNING/INFO are advisory
    code: stable machine-readable identifier (e.g. "SLOT_OVERLAP")
    message: human-readable explanation
    source_locations: zero or more source references
    affected_entities: structured context (teacher, day, slot, etc.)
    suggestion: optional actionable resolution guidance
    """
    
    severity: ValidationSeverity
    code: str
    message: str
    source_locations: list[SourceLocation] = field(default_factory=list)
    affected_entities: dict[str, str] = field(default_factory=dict)
    suggestion: str | None = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "source_locations": [loc.to_display() for loc in self.source_locations],
            "affected_entities": self.affected_entities,
            "suggestion": self.suggestion,
        }


# ---------------------------------------------------------------------------
# Teacher Identity
# ---------------------------------------------------------------------------


@dataclass
class TeacherIdentity:
    """
    Stable teacher reference within a CanonicalTimetable.
    
    Avoids duplicating teacher information across multiple activities.
    Supports both resolved (existing DB teacher) and unresolved (new) teachers.
    
    IMPORTANT: Teacher identity is scoped by (acronym, department).
    Two teachers with the same acronym in different departments are distinct identities.
    
    resolved_teacher_id: UUID of existing teacher in DB (None if new)
    acronym: stable identifier within department
    name: full name
    level: UG/PG
    program_name: program name (e.g. "MCA")
    semester: semester number
    department: department name (part of teacher identity)
    """
    
    acronym: str
    name: str
    level: str
    program_name: str
    semester: int
    department: str
    resolved_teacher_id: uuid.UUID | None = None
    
    # Action determined during validation
    action: Literal["CREATE", "REUSE", "CONFLICT"] = "CREATE"
    
    # Validation issues specific to this teacher
    issues: list[ValidationIssue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Activity Slot Range
# ---------------------------------------------------------------------------


@dataclass
class ActivitySlotRange:
    """
    Day and slot occupancy for one schedule activity.
    
    Represents the when/where of an activity:
    - day: ISO weekday (1=Monday, 7=Sunday)
    - slot_codes: ordered list of slot codes (e.g. ["S4", "S5"] for a 2-slot LAB)
    - source_location: optional traceability to import source
    """
    
    day_of_week: int  # ISO: 1=Monday, 7=Sunday
    slot_codes: list[str]
    source_location: SourceLocation | None = None


# ---------------------------------------------------------------------------
# Schedule Activity
# ---------------------------------------------------------------------------


@dataclass
class ScheduleActivity:
    """
    ONE logical schedule activity (may span multiple time slots).
    
    Represents the what/who/where of an activity:
    - teacher_acronym: references TeacherIdentity.acronym in parent CanonicalTimetable
    - entry_type: CLASS/LAB/OTHER
    - subject_or_activity: subject name or activity description
    - section: class section (optional)
    - room: legacy free-text room field (deprecated, use resource_ref)
    - resource_ref: normalized resource reference (NEW)
    - notes: additional notes (optional)
    - slot_range: when this activity occurs
    - issues: validation issues specific to this activity
    """
    
    teacher_acronym: str
    entry_type: Literal["CLASS", "LAB", "OTHER"]
    subject_or_activity: str | None
    section: str | None
    room: str | None  # Legacy field, preserved for backward compatibility
    notes: str | None
    slot_range: ActivitySlotRange
    issues: list[ValidationIssue] = field(default_factory=list)
    resource_ref: "ResourceReference | None" = None  # NEW: normalized resource reference


# ---------------------------------------------------------------------------
# Canonical Timetable
# ---------------------------------------------------------------------------


@dataclass
class CanonicalTimetable:
    """
    Source-agnostic canonical representation of a timetable import.
    
    This is the shared domain model used by:
    - XLSX import
    - DOCX import (future)
    - Direct editing (future)
    - Validation engine
    - Persistence layer
    
    Structure:
    - import_id: unique identifier for this import/edit session
    - academic_year: target academic year (e.g. "2026-2027")
    - teachers: stable collection of teacher identities
    - activities: schedule activities referencing teachers by acronym
    - global_issues: validation issues not tied to specific teacher/activity
    
    Design notes:
    - Teachers are stored once to avoid duplication/conflicts
    - Activities reference teachers by acronym (stable within this timetable)
    - Source locations preserve traceability to XLSX/DOCX/MANUAL origins
    - Validation issues are structured and categorized by severity
    """
    
    import_id: str
    academic_year: str
    teachers: list[TeacherIdentity] = field(default_factory=list)
    activities: list[ScheduleActivity] = field(default_factory=list)
    global_issues: list[ValidationIssue] = field(default_factory=list)
    
    def get_teacher(self, acronym: str) -> TeacherIdentity | None:
        """Retrieve teacher by acronym."""
        for teacher in self.teachers:
            if teacher.acronym == acronym:
                return teacher
        return None
    
    def has_errors(self) -> bool:
        """Check if any ERROR-level issues exist."""
        if any(issue.severity == ValidationSeverity.ERROR for issue in self.global_issues):
            return True
        for teacher in self.teachers:
            if any(issue.severity == ValidationSeverity.ERROR for issue in teacher.issues):
                return True
        for activity in self.activities:
            if any(issue.severity == ValidationSeverity.ERROR for issue in activity.issues):
                return True
        return False
    
    def get_activities_by_teacher(self, acronym: str) -> list[ScheduleActivity]:
        """Get all activities for a specific teacher."""
        return [act for act in self.activities if act.teacher_acronym == acronym]
