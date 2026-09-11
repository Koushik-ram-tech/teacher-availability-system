"""
excel_import.normalizer
-----------------------
Converts raw parser output into a typed ImportPreview with syntax validation.

This module does NOT touch the database.  It validates:
  - academic year format
  - teacher row fields (name, acronym uniqueness within sheet, level, semester)
  - schedule row syntax (day, type, slot parsing and de-duplication)
  - LAB consecutive-slot rules (LAB must be exactly 2 consecutive slots)
  - CLASS/OTHER slot rules (1 or 2 consecutive slots permitted)
  - intra-workbook duplicate slot occupancy per teacher/day

Semester defaulting: when no numeric 'semester' column is present (normal for
the official 2-sheet workbook which uses 'semester_scope' for display only),
the DB field is silently defaulted to 1.  This is routine and produces no
advisory warning.

DB-level validation (program resolution, existing teacher conflicts) is the
responsibility of ``validator.py``.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from app.core.schedule_config import (
    SLOT_CODES,
    VALID_DAY_NAMES,
    VALID_LAB_PAIRS,
    is_valid_academic_year,
)
from app.schemas.imports import ImportPreview, ScheduleImportRow, TeacherImportRow
from app.services.excel_import.time_to_slots import TimeConversionError, time_range_to_slots

# Slot delimiters accepted in the legacy ``slots`` cell: + , / or any whitespace
_SLOT_DELIMITERS = re.compile(r"[+,/\s]+")

VALID_LEVELS: frozenset[str] = frozenset({"UG", "PG"})
VALID_TYPES: frozenset[str] = frozenset({"CLASS", "LAB", "OTHER"})


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def normalize(raw: dict[str, Any]) -> ImportPreview:
    """
    Normalise raw parser output into an :class:`ImportPreview`.

    All errors are accumulated; the preview is always returned even when errors
    exist so that the API can surface row-level detail to the caller.
    Fatal errors (``preview.errors`` non-empty) will prevent confirmation.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------ #
    # Metadata                                                             #
    # ------------------------------------------------------------------ #

    meta = raw["metadata"]
    academic_year: str = meta["academic_year"].strip()
    if not is_valid_academic_year(academic_year):
        errors.append(
            f"Metadata: academic_year '{academic_year}' is invalid. "
            "Expected YYYY-YYYY where the second year equals first + 1 "
            "(e.g. 2025-2026)."
        )

    # ------------------------------------------------------------------ #
    # Teachers sheet                                                       #
    # ------------------------------------------------------------------ #

    teacher_rows: list[TeacherImportRow] = []
    # Track (acronym, department) combinations already seen in *this workbook*
    # to catch sheet-level duplicates. Department is now part of teacher identity.
    seen_identities: dict[tuple[str, str], str] = {}  # (normalized_acronym, normalized_dept) → row_ref

    for raw_t in raw["teachers"]:
        row_ref: str = raw_t["row_ref"]
        row_errors: list[str] = []

        # name
        name_raw = raw_t.get("name")
        name = name_raw.strip() if name_raw else ""
        if not name:
            row_errors.append(f"{row_ref}: teacher name is required.")

        # acronym
        acronym_raw = raw_t.get("acronym")
        acronym = acronym_raw.strip().upper() if acronym_raw else ""
        if not acronym:
            row_errors.append(f"{row_ref}: acronym is required.")

        # level
        level_raw = raw_t.get("level")
        level = level_raw.strip().upper() if level_raw else ""
        if level not in VALID_LEVELS:
            row_errors.append(
                f"{row_ref}: level '{level_raw}' is invalid. Expected 'UG' or 'PG'."
            )

        # program
        program_raw = raw_t.get("program")
        program_name = program_raw.strip() if program_raw else ""
        if not program_name:
            row_errors.append(f"{row_ref}: program is required.")

        # ---------------------------------------------------------------- #
        # semester — DB requires NOT NULL > 0.                              #
        #                                                                    #
        # semester_scope ('I & III Semester', 'All Semesters', etc.) is     #
        # human context only; it is NOT treated as an integer source.       #
        # Roman numerals (I, III) are not Arabic digits and must NOT be     #
        # parsed as such.  The actual teaching section and semester are      #
        # recorded in Schedule.section (e.g. 'I-A', 'III-B'), not here.    #
        #                                                                    #
        # Strategy (in priority order):                                      #
        #   1. Explicit 'semester' column present and is a positive integer  #
        #      → use it as-is.                                              #
        #   2. Otherwise → default to 1 (smallest valid DB value) and emit  #
        #      an informational warning. The caller should record the actual #
        #      teaching section in Schedule.section instead.                 #
        # ---------------------------------------------------------------- #
        semester_raw = raw_t.get("semester")
        semester: int | None = None

        if semester_raw is not None:
            # Explicit integer column provided — parse strictly
            try:
                semester = int(float(str(semester_raw)))
                if semester <= 0:
                    row_errors.append(
                        f"{row_ref}: semester must be a positive integer, got {semester}."
                    )
                    semester = None
            except (TypeError, ValueError):
                row_errors.append(
                    f"{row_ref}: semester '{semester_raw}' is not a valid positive integer."
                )
        else:
            # No explicit integer provided — use compatibility default.
            # semester_scope is treated as display-only context; it is not
            # parsed for numbers because Roman numerals (I, III) and phrases
            # like 'All Semesters' are not reliably convertible to a single int.
            # This is normal for the official 2-sheet workbook format; no
            # advisory warning is emitted.
            semester = 1

        # department (optional; DB default = 'Prototype Department')
        dept_raw = raw_t.get("department")
        department = (dept_raw.strip() if dept_raw else "") or "Prototype Department"

        # Check for duplicate (acronym, department) within this workbook
        # Department is part of teacher identity - same acronym in different departments is allowed
        if acronym and department:
            identity_key = (acronym, department.upper())
            if identity_key in seen_identities:
                row_errors.append(
                    f"{row_ref}: duplicate teacher identity (acronym='{acronym}', department='{department}') "
                    f"(first seen at {seen_identities[identity_key]})."
                )
            else:
                seen_identities[identity_key] = row_ref

        errors.extend(row_errors)
        if not row_errors and name and acronym and level in VALID_LEVELS and program_name and semester:
            teacher_rows.append(
                TeacherImportRow(
                    row_ref=row_ref,
                    action="CREATE",  # updated by validator after DB lookup
                    name=name,
                    acronym=acronym,
                    level=level,
                    program_name=program_name,
                    semester=semester,
                    department=department,
                    resolved_teacher_id=None,
                    warnings=[],
                )
            )

    # Acronyms that passed normalisation — used to validate Schedule rows
    # Build acronym → set of departments mapping to detect ambiguity
    acronym_to_departments: dict[str, set[str]] = {}
    for t in teacher_rows:
        acronym_to_departments.setdefault(t.acronym, set()).add(t.department.upper())

    # ---------------------------------------------------------------------- #
    # Schedule sheet                                                       #
    # ---------------------------------------------------------------------- #

    days: dict[str, list[ScheduleImportRow]] = {}
    # Collision tracking: (acronym, day) → set of slot codes already claimed
    teacher_day_slots: dict[tuple[str, str], set[str]] = {}

    for raw_s in raw["schedule"]:
        row_ref = raw_s["row_ref"]
        row_errors = []

        # teacher_acronym
        ta_raw = raw_s.get("teacher_acronym")
        teacher_acronym = ta_raw.strip().upper() if ta_raw else ""
        if not teacher_acronym:
            row_errors.append(f"{row_ref}: teacher_acronym is required.")
        elif teacher_acronym not in acronym_to_departments:
            row_errors.append(
                f"{row_ref}: teacher_acronym '{teacher_acronym}' not found in Teachers sheet."
            )
        elif len(acronym_to_departments[teacher_acronym]) > 1:
            # AMBIGUOUS: Same acronym exists in multiple departments
            depts = sorted(acronym_to_departments[teacher_acronym])
            row_errors.append(
                f"{row_ref}: teacher_acronym '{teacher_acronym}' is ambiguous - "
                f"found in departments: {', '.join(depts)}. "
                "Cannot determine which teacher this schedule entry belongs to. "
                "Use unique acronyms or separate imports per department."
            )

        # day
        day_raw = raw_s.get("day")
        day = day_raw.strip().lower() if day_raw else ""
        if not day:
            row_errors.append(f"{row_ref}: day is required.")
        elif day == "sunday":
            row_errors.append(f"{row_ref}: Sunday is not a valid working day.")
            day = ""
        elif day not in VALID_DAY_NAMES:
            row_errors.append(
                f"{row_ref}: day '{day_raw}' is invalid. "
                f"Expected one of: {sorted(VALID_DAY_NAMES)}."
            )
            day = ""

        # type / entry_type
        type_raw = raw_s.get("type")
        entry_type = type_raw.strip().upper() if type_raw else ""
        if not entry_type:
            row_errors.append(f"{row_ref}: type is required.")
        elif entry_type not in VALID_TYPES:
            row_errors.append(
                f"{row_ref}: type '{type_raw}' is invalid. "
                "Expected CLASS, LAB, or OTHER."
            )
            entry_type = ""

        # ---------------------------------------------------------------- #
        # Slot resolution                                                    #
        #                                                                    #
        # Priority: 'time' column (final workbook) is authoritative.        #
        # Legacy 'slots' column (S-codes) is accepted as a fallback when    #
        # 'time' is absent.                                                  #
        #                                                                    #
        # If BOTH columns are present in the same row, both are resolved     #
        # and compared.  Agreement → accept; contradiction → validation error#
        # ---------------------------------------------------------------- #
        time_raw = raw_s.get("time")
        slots_raw = raw_s.get("slots")
        slot_ids: list[str] = []

        time_slot_ids: list[str] | None = None
        slots_slot_ids: list[str] | None = None

        if time_raw:
            try:
                time_slot_ids = time_range_to_slots(time_raw)
            except TimeConversionError as exc:
                row_errors.append(f"{row_ref}: {exc}")

        if slots_raw:
            parts = [
                p.strip().upper()
                for p in _SLOT_DELIMITERS.split(str(slots_raw))
                if p.strip()
            ]
            seen_in_row: set[str] = set()
            legacy_ids: list[str] = []
            legacy_errors: list[str] = []
            for code in parts:
                if code in seen_in_row:
                    continue
                seen_in_row.add(code)
                if code not in SLOT_CODES:
                    legacy_errors.append(
                        f"{row_ref}: Slot '{code}' is invalid. "
                        "Expected one of S1–S9."
                    )
                else:
                    legacy_ids.append(code)
            if not legacy_errors:
                slots_slot_ids = sorted(legacy_ids, key=lambda c: SLOT_CODES.index(c))
            else:
                row_errors.extend(legacy_errors)

        # Decide which resolved value to use
        if time_slot_ids is not None and slots_slot_ids is not None:
            # Both present and both resolved — check for contradiction
            if time_slot_ids != slots_slot_ids:
                row_errors.append(
                    f"{row_ref}: 'time' column resolves to {time_slot_ids} but "
                    f"'slots' column resolves to {slots_slot_ids}. "
                    "Remove the contradiction or make them agree."
                )
            else:
                slot_ids = time_slot_ids  # they agree
        elif time_slot_ids is not None:
            slot_ids = time_slot_ids
        elif slots_slot_ids is not None:
            slot_ids = slots_slot_ids
        elif not row_errors:
            # Neither column provided any value
            row_errors.append(
                f"{row_ref}: 'time' column is required (e.g. '8:00 AM - 8:55 AM')."
            )

        # Validate slot/type combination (only when both are valid so far)
        #
        # Rules (updated):
        #   - Any activity must occupy exactly 1 or 2 slots.
        #   - If 2 slots, they must form a valid consecutive pair (no break
        #     between them) — this applies to ALL types including CLASS/OTHER.
        #   - LAB must occupy exactly 2 consecutive slots.
        #   - CLASS and OTHER may occupy 1 OR 2 consecutive slots.
        if entry_type and slot_ids:
            # LAB-specific count check runs unconditionally (even for 3+ slots)
            # so that the error message always references "LAB" explicitly.
            if entry_type == "LAB" and len(slot_ids) != 2:
                row_errors.append(
                    f"{row_ref}: LAB must occupy exactly two consecutive slots "
                    f"(got {len(slot_ids)})."
                )
            elif len(slot_ids) not in (1, 2):
                # Non-LAB activity with wrong slot count
                row_errors.append(
                    f"{row_ref}: Schedule activity must occupy 1 or 2 slots "
                    f"(got {len(slot_ids)})."
                )
            else:
                # Slot count is 1 or 2 — for 2 slots verify they form a
                # valid consecutive pair (no break between them).
                if len(slot_ids) == 2:
                    pair = frozenset(slot_ids)
                    if pair not in VALID_LAB_PAIRS:
                        valid_str = ", ".join(
                            f"{sorted(p)[0]}+{sorted(p)[1]}"
                            for p in sorted(VALID_LAB_PAIRS, key=sorted)
                        )
                        row_errors.append(
                            f"{row_ref}: Multiple slots must be a valid "
                            f"consecutive pair (no break between them). "
                            f"Valid pairs: {valid_str}."
                        )

        # Intra-workbook duplicate slot collision per teacher/day
        if teacher_acronym and day and slot_ids and not row_errors:
            key = (teacher_acronym, day)
            used = teacher_day_slots.setdefault(key, set())
            overlap = used & set(slot_ids)
            if overlap:
                row_errors.append(
                    f"{row_ref}: Teacher {teacher_acronym} already occupies "
                    f"slot(s) {sorted(overlap)} on {day} (within this workbook)."
                )
            else:
                used.update(slot_ids)

        errors.extend(row_errors)

        # Only add the row to the preview if it passed all checks
        if not row_errors and teacher_acronym and day and entry_type and slot_ids:
            sched_row = ScheduleImportRow(
                row_refs=[row_ref],
                teacher_acronym=teacher_acronym,
                day=day,
                slot_ids=slot_ids,
                entry_type=entry_type,
                subject_or_activity=raw_s.get("subject_or_activity"),
                section=raw_s.get("section"),
                room=raw_s.get("room"),
                notes=raw_s.get("notes"),
                warnings=[],
            )
            days.setdefault(day, []).append(sched_row)

    return ImportPreview(
        import_id=str(uuid.uuid4()),
        academic_year=academic_year,
        teachers=teacher_rows,
        days=days,
        warnings=warnings,
        errors=errors,
    )
