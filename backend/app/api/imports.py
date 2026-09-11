"""
imports.py — FastAPI router for Excel workbook import.

Endpoints (all under /api/v1):

  POST   /imports/excel              Upload .xlsx → parse → stage → return preview
  POST   /imports/{import_id}/confirm  Validate staged preview → persist transactionally
  GET    /imports/{import_id}        Retrieve staged preview (optional)
  DELETE /imports/{import_id}        Discard staged preview (optional)

Staging uses a module-level in-memory dict.  Losing it on restart is
acceptable for this prototype.

HTTP status codes follow existing project conventions:
  400 = malformed workbook / bad request
  404 = import_id not found
  422 = semantic validation failure (errors prevent confirmation)
  500 = unexpected server error (all changes rolled back)
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.converters import canonical_to_import_preview, import_preview_to_canonical
from app.domain.timetable import CanonicalTimetable
from app.domain.validation import validate_canonical
from app.schemas.imports import ImportConfirmOut, ImportPreview
from app.services.excel_import.normalizer import normalize
from app.services.excel_import.parser import ParseError, parse_workbook
from app.services.excel_import.persister import PersistenceError, persist_import

router = APIRouter(prefix="/imports", tags=["imports"])

# ---------------------------------------------------------------------------
# In-memory staging store
# ---------------------------------------------------------------------------
# Maps import_id (UUID string) → CanonicalTimetable.
# Cleared on app restart — this is by design for the prototype.
# 
# DESIGN NOTE: We store the canonical representation, not ImportPreview.
# This allows future DOCX/Editor sources to use the same staging mechanism.
# ImportPreview is derived on-demand for API responses.
_STAGING: dict[str, CanonicalTimetable] = {}

MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024  # 10 MB


# ---------------------------------------------------------------------------
# POST /imports/excel
# ---------------------------------------------------------------------------


@router.post(
    "/excel",
    status_code=status.HTTP_200_OK,
    response_model=ImportPreview,
    summary="Upload an Excel workbook for preview (no DB writes)",
)
async def upload_excel(
    file: Annotated[UploadFile, File(description="Excel .xlsx workbook (Teachers + Schedule sheets only)")],
    academic_year: Annotated[str, Form(description="Academic year e.g. 2026-2027")],
    db: Session = Depends(get_db),
) -> ImportPreview:
    """
    Parse an uploaded .xlsx workbook, validate it, and return a normalised
    preview.  No timetable rows are written to the database at this stage.

    The workbook must contain exactly two sheets: Teachers and Schedule.
    The Metadata sheet is no longer required or accepted.

    The ``academic_year`` form field (e.g. ``2026-2027``) is validated
    using the same rules as the old Metadata sheet value.

    The returned ``import_id`` is used with the confirm endpoint.
    """
    # --- File type guard ---
    filename: str = file.filename or ""
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .xlsx files are accepted.",
        )

    data: bytes = await file.read()

    if len(data) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )
    if len(data) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {mb} MB.",
        )

    # --- Parse (structural) ---
    try:
        raw = parse_workbook(data, academic_year.strip())
    except ParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # --- Normalise (syntax + basic rule validation, no DB) ---
    preview = normalize(raw)

    # --- Convert to canonical domain model ---
    canonical = import_preview_to_canonical(preview)

    # --- Validate against domain rules and DB state ---
    canonical = validate_canonical(canonical, db)

    # --- Stage canonical representation ---
    _STAGING[canonical.import_id] = canonical

    # --- Convert to ImportPreview for API response ---
    return canonical_to_import_preview(canonical)


# ---------------------------------------------------------------------------
# GET /imports/{import_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{import_id}",
    response_model=ImportPreview,
    summary="Retrieve a staged import preview",
)
def get_import(import_id: str) -> ImportPreview:
    """Return the staged import preview identified by *import_id*."""
    canonical = _STAGING.get(import_id)
    if canonical is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Import '{import_id}' not found. "
                "It may have expired, been confirmed, or never existed."
            ),
        )
    # Convert canonical to preview for API response
    return canonical_to_import_preview(canonical)


# ---------------------------------------------------------------------------
# DELETE /imports/{import_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{import_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Discard a staged import",
)
def delete_import(import_id: str) -> None:
    """Discard a staged import without persisting it."""
    if import_id not in _STAGING:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Import '{import_id}' not found.",
        )
    del _STAGING[import_id]


# ---------------------------------------------------------------------------
# POST /imports/{import_id}/confirm
# ---------------------------------------------------------------------------


@router.post(
    "/{import_id}/confirm",
    response_model=ImportConfirmOut,
    summary="Persist a staged import (ALL-OR-NOTHING)",
)
def confirm_import(
    import_id: str,
    db: Session = Depends(get_db),
) -> ImportConfirmOut:
    """
    Persist a staged import.

    - Re-validates against current DB state.
    - Rejects if any errors remain.
    - Creates/reuses teachers and creates DRAFT timetables in **one transaction**.
    - Never creates or modifies CONFIRMED timetables.
    - Rolls back **all** changes if any step fails.
    - Removes the staging entry on success.
    """
    canonical = _STAGING.get(import_id)
    if canonical is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Import '{import_id}' not found. "
                "It may have expired, been confirmed, or never existed."
            ),
        )

    # Re-validate against current DB (state may have changed since upload)
    canonical = validate_canonical(canonical, db)

    # Block on any errors
    if canonical.has_errors():
        # Convert to preview for error response
        preview = canonical_to_import_preview(canonical)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Validation errors prevent confirmation.",
                "errors": preview.errors,
            },
        )

    # Convert to preview for persistence (persister expects ImportPreview)
    preview = canonical_to_import_preview(canonical)

    # Persist (all flushes; caller session commits at the end)
    try:
        result = persist_import(preview, db)
        db.commit()
    except PersistenceError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "An unexpected error occurred during import persistence. "
                "All changes have been rolled back."
            ),
        ) from exc

    # Remove from staging on success
    _STAGING.pop(import_id, None)

    return ImportConfirmOut(
        import_id=import_id,
        academic_year=preview.academic_year,
        teachers_created=result["teachers_created"],
        teachers_reused=result["teachers_reused"],
        timetables_created=result["timetables_created"],
        timetables_replaced=result["timetables_replaced"],
    )
