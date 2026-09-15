"""
Shared import staging storage.

This module provides a centralized in-memory staging mechanism for all
import workflows (XLSX, DOCX, etc.).

IMPORTANT: This is prototype-level storage. In production, consider using
Redis or similar for distributed/persistent staging.
"""
from __future__ import annotations

from app.domain.timetable import CanonicalTimetable
from app.schemas.docx_imports import DOCXImportPreview

# ---------------------------------------------------------------------------
# Staging stores
# ---------------------------------------------------------------------------

# Maps import_id → CanonicalTimetable (for imports ready to persist)
# Used by both XLSX and DOCX workflows
_CANONICAL_STAGING: dict[str, CanonicalTimetable] = {}

# Maps import_id → DOCXImportPreview (for DOCX imports with unresolved blocks)
# Stores DOCX-specific preview data during manual resolution workflow
_DOCX_STAGING: dict[str, DOCXImportPreview] = {}


# ---------------------------------------------------------------------------
# Canonical staging accessors
# ---------------------------------------------------------------------------

def stage_canonical(import_id: str, canonical: CanonicalTimetable) -> None:
    """Stage a canonical timetable for confirmation."""
    _CANONICAL_STAGING[import_id] = canonical


def get_canonical(import_id: str) -> CanonicalTimetable | None:
    """Retrieve a staged canonical timetable."""
    return _CANONICAL_STAGING.get(import_id)


def remove_canonical(import_id: str) -> None:
    """Remove a staged canonical timetable."""
    _CANONICAL_STAGING.pop(import_id, None)


# ---------------------------------------------------------------------------
# DOCX staging accessors
# ---------------------------------------------------------------------------

def stage_docx_preview(import_id: str, preview: DOCXImportPreview) -> None:
    """Stage a DOCX preview for resolution workflow."""
    _DOCX_STAGING[import_id] = preview


def get_docx_preview(import_id: str) -> DOCXImportPreview | None:
    """Retrieve a staged DOCX preview."""
    return _DOCX_STAGING.get(import_id)


def update_docx_preview(import_id: str, preview: DOCXImportPreview) -> None:
    """Update a staged DOCX preview (after resolution/finalization)."""
    _DOCX_STAGING[import_id] = preview


def remove_docx_preview(import_id: str) -> None:
    """Remove a staged DOCX preview."""
    _DOCX_STAGING.pop(import_id, None)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def has_import(import_id: str) -> bool:
    """Check if an import exists in either staging dict."""
    return import_id in _CANONICAL_STAGING or import_id in _DOCX_STAGING


def get_import_type(import_id: str) -> str | None:
    """Determine the type of import ('XLSX', 'DOCX', or None if not found)."""
    if import_id in _DOCX_STAGING:
        return "DOCX"
    if import_id in _CANONICAL_STAGING:
        return "XLSX"  # Or could be a finalized DOCX
    return None
