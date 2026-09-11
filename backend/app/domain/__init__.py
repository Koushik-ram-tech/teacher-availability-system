"""
domain — Canonical domain models for timetable import and management.

This package contains the shared canonical representation used by:
- XLSX import
- Future DOCX import
- Future direct editing
- Validation engine
- Persistence

The domain model is source-agnostic and represents the institutional
rules and structure, not the transport/UI format.
"""
from __future__ import annotations

from app.domain.converters import canonical_to_import_preview, import_preview_to_canonical
from app.domain.resources import (
    ResourceReference,
    ResourceResolutionStatus,
    ResourceResolver,
    normalize_resource_name,
)
from app.domain.timetable import (
    ActivitySlotRange,
    CanonicalTimetable,
    ScheduleActivity,
    SourceLocation,
    TeacherIdentity,
    ValidationIssue,
    ValidationSeverity,
)
from app.domain.validation import validate_canonical

__all__ = [
    "ActivitySlotRange",
    "CanonicalTimetable",
    "ScheduleActivity",
    "SourceLocation",
    "TeacherIdentity",
    "ValidationIssue",
    "ValidationSeverity",
    "canonical_to_import_preview",
    "import_preview_to_canonical",
    "validate_canonical",
    "ResourceReference",
    "ResourceResolutionStatus",
    "ResourceResolver",
    "normalize_resource_name",
]
