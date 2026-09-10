"""
excel_import.parser
-------------------
Reads a raw .xlsx workbook and extracts each sheet as a list of plain dicts.

This module performs STRUCTURAL parsing only:
  - verifies the workbook can be opened
  - verifies EXACTLY two required sheets are present: Teachers, Schedule
  - verifies each sheet has all required column headers
  - extracts data rows as dicts keyed by normalised column name

The academic_year is NO LONGER read from the workbook.  It is supplied by
the caller (API layer) as a separate form field and injected into the result
dict so the normalizer can validate it with the same rules as before.

Business-rule validation (slot codes, academic year format, program
resolution, etc.) is the responsibility of the normalizer and validator.

Raises ParseError for any unrecoverable structural problem.
"""
from __future__ import annotations

import io
from typing import Any

import openpyxl
from openpyxl.workbook import Workbook

# ---------------------------------------------------------------------------
# Required sheet names and their required column sets
# ---------------------------------------------------------------------------

REQUIRED_SHEETS: frozenset[str] = frozenset({"teachers", "schedule"})

_REQUIRED_COLS: dict[str, frozenset[str]] = {
    # 'semester' was the old internal column name.
    # The final college-facing workbook uses 'semester_scope' (optional human
    # context; the DB semester field is derived from it or defaulted to 1).
    "teachers": frozenset({"name", "acronym", "level", "program"}),
    # Schedule requires teacher_acronym, day, type, and EITHER 'time' (final
    # workbook) or 'slots' (legacy internal format).  The OR check happens in
    # _parse_schedule after the header row is read.
    "schedule": frozenset({"teacher_acronym", "day", "type"}),
}


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class ParseError(Exception):
    """Raised on an unrecoverable workbook structure problem."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_workbook(data: bytes) -> Workbook:
    """Open an .xlsx workbook from raw bytes. Raises ParseError on failure."""
    try:
        return openpyxl.load_workbook(
            filename=io.BytesIO(data),
            data_only=True,   # read computed cell values, never evaluate formulas
            read_only=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Cannot open workbook: {exc}") from exc


def _sheet_by_canonical(wb: Workbook, canonical: str):
    """Return the worksheet whose name matches *canonical* case-insensitively."""
    for name in wb.sheetnames:
        if name.strip().lower() == canonical:
            return wb[name]
    return None


def _parse_headers(sheet, sheet_canonical: str) -> dict[str, int]:
    """
    Read row 1 as headers.
    Returns {normalised_col_name: 0-based column index}.
    Raises ParseError if any required column is missing.
    """
    headers: dict[str, int] = {}
    first_row = next(sheet.iter_rows(max_row=1, values_only=True), None)
    if first_row is None:
        raise ParseError(f"Sheet '{sheet_canonical}' has no header row.")

    for col_idx, cell in enumerate(first_row):
        if cell is not None:
            key = str(cell).strip().lower()
            if key:
                headers[key] = col_idx

    required = _REQUIRED_COLS[sheet_canonical]
    missing = required - set(headers)
    if missing:
        raise ParseError(
            f"Sheet '{sheet_canonical}' is missing required column(s): "
            f"{sorted(missing)}.  Found: {sorted(headers)}."
        )
    return headers


def _cell_str(value: Any) -> str | None:
    """Coerce a cell value to a stripped string, or None if blank."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _is_blank_row(row: tuple) -> bool:
    return all(v is None or str(v).strip() == "" for v in row)


# ---------------------------------------------------------------------------
# Sheet parsers
# ---------------------------------------------------------------------------


def _parse_teachers(sheet) -> list[dict[str, Any]]:
    headers = _parse_headers(sheet, "teachers")

    results: list[dict[str, Any]] = []
    for row_num, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if _is_blank_row(row):
            continue
        results.append({
            "row_ref": f"Teachers!{row_num}",
            "name": _cell_str(row[headers["name"]]),
            "acronym": _cell_str(row[headers["acronym"]]),
            "level": _cell_str(row[headers["level"]]),
            "program": _cell_str(row[headers["program"]]),
            # semester_scope is optional human context from the final workbook.
            # Kept raw; normalizer decides how to derive the DB semester value.
            "semester_scope": (
                _cell_str(row[headers["semester_scope"]])
                if "semester_scope" in headers
                else None
            ),
            # legacy 'semester' column still accepted if present (backward compat)
            "semester": (
                row[headers["semester"]]
                if "semester" in headers
                else None
            ),
            "department": (
                _cell_str(row[headers["department"]])
                if "department" in headers
                else None
            ),
        })
    return results


def _parse_schedule(sheet) -> list[dict[str, Any]]:
    headers = _parse_headers(sheet, "schedule")

    # Enforce OR requirement: workbook must contain 'time' (final) or 'slots' (legacy)
    if "time" not in headers and "slots" not in headers:
        raise ParseError(
            "Sheet 'schedule' must contain either a 'time' column "
            "(e.g. '8:00 AM - 8:55 AM') or a legacy 'slots' column (e.g. 'S1')."
        )

    results: list[dict[str, Any]] = []
    for row_num, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if _is_blank_row(row):
            continue
        results.append({
            "row_ref": f"Schedule!{row_num}",
            "teacher_acronym": _cell_str(row[headers["teacher_acronym"]]),
            "day": _cell_str(row[headers["day"]]),
            "type": _cell_str(row[headers["type"]]),
            # Final workbook uses 'time' column (human-readable range).
            # Legacy 'slots' column still accepted if 'time' is absent.
            "time": _cell_str(row[headers["time"]]) if "time" in headers else None,
            "slots": _cell_str(row[headers["slots"]]) if "slots" in headers else None,
            "subject_or_activity": _cell_str(row[headers["subject_or_activity"]]) if "subject_or_activity" in headers else None,
            "section": _cell_str(row[headers["section"]]) if "section" in headers else None,
            "room": _cell_str(row[headers["room"]]) if "room" in headers else None,
        })
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_workbook(data: bytes, academic_year: str) -> dict[str, Any]:
    """
    Parse an .xlsx workbook from raw bytes into a plain dict.

    ``academic_year`` is injected from the API form field — the workbook
    must NOT contain a Metadata sheet; if one is present it is silently
    ignored so that users who accidentally include it are not blocked.

    Returns::

        {
            "metadata": {"academic_year": str},   # from form field, not workbook
            "teachers": [{"row_ref": str, "name": str | None, ...}, ...],
            "schedule": [{"row_ref": str, "teacher_acronym": str | None, ...}, ...],
        }

    Raises :class:`ParseError` for any structural problem (bad file,
    missing required sheet, missing header column).
    """
    wb = _load_workbook(data)

    present = {name.strip().lower() for name in wb.sheetnames}
    missing_sheets = REQUIRED_SHEETS - present
    if missing_sheets:
        raise ParseError(
            f"Workbook is missing required sheet(s): {sorted(missing_sheets)}. "
            f"Found: {sorted(present)}. "
            "The workbook must contain exactly 'Teachers' and 'Schedule' sheets. "
            "No Metadata sheet is needed — supply the academic year in the upload form."
        )

    teachers = _parse_teachers(_sheet_by_canonical(wb, "teachers"))
    schedule = _parse_schedule(_sheet_by_canonical(wb, "schedule"))

    wb.close()
    return {
        "metadata": {"academic_year": academic_year},  # injected from form field
        "teachers": teachers,
        "schedule": schedule,
    }
