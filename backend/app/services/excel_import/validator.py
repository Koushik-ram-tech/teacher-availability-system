"""
excel_import.validator
----------------------
Database-level validation of an ImportPreview.

Responsibilities:
  - Resolve each teacher's program via (name, level) lookup.
  - Check whether a teacher with the same normalised acronym already exists.
  - Set TeacherImportRow.action to CREATE / REUSE / CONFLICT.
  - Propagate CONFLICT errors to schedule rows that reference the conflicted teacher.

Uses ``func.lower()`` for case-insensitive comparisons to be compatible with
both SQLite (unit tests) and PostgreSQL (integration / production).
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import Program, Teacher
from app.schemas.imports import ImportPreview, TeacherImportRow


def resolve_and_validate(preview: ImportPreview, db: Session) -> ImportPreview:
    """
    Validate the preview against the current DB state.

    Returns a *new* preview (Pydantic models are immutable-ish) with:
      - teacher actions updated to CREATE / REUSE / CONFLICT
      - resolved_teacher_id populated for REUSE rows
      - identity-conflict errors appended to preview.errors
    """
    errors: list[str] = list(preview.errors)
    updated_teachers: list[TeacherImportRow] = []

    for t in preview.teachers:
        row_errors: list[str] = []
        row_warnings: list[str] = list(t.warnings)

        # ---------------------------------------------------------------- #
        # 1. Resolve program by (name, level) — case-insensitive           #
        # ---------------------------------------------------------------- #
        program = (
            db.query(Program)
            .filter(
                func.lower(Program.name) == t.program_name.lower(),
                Program.level == t.level,
                Program.is_active.is_(True),
            )
            .first()
        )

        if program is None:
            # Try to find the program with a different level to give a better message
            any_level_prog = (
                db.query(Program)
                .filter(func.lower(Program.name) == t.program_name.lower())
                .first()
            )
            if any_level_prog:
                row_errors.append(
                    f"{t.row_ref}: Program '{t.program_name}' is registered in DB "
                    f"at level '{any_level_prog.level}', but this teacher row specifies "
                    f"level '{t.level}'. Correct the workbook or DB."
                )
            else:
                row_errors.append(
                    f"{t.row_ref}: Unknown program '{t.program_name}' at level '{t.level}'. "
                    "Ensure the program exists in the database before importing."
                )

        # ---------------------------------------------------------------- #
        # 2. Check for existing teacher with same (normalized acronym, department)
        #    Department is now part of teacher identity.
        # ---------------------------------------------------------------- #
        existing: Teacher | None = (
            db.query(Teacher)
            .filter(
                func.lower(Teacher.acronym) == t.acronym.lower(),
                func.lower(Teacher.department) == t.department.lower(),
            )
            .first()
        )

        action: str = "CREATE"
        resolved_id = None

        if existing is not None:
            resolved_id = existing.id
            # Check material identity fields for conflicts
            conflicts: list[str] = []
            if existing.name.strip().lower() != t.name.lower():
                conflicts.append(
                    f"name (DB: '{existing.name}', workbook: '{t.name}')"
                )
            if existing.level != t.level:
                conflicts.append(
                    f"level (DB: '{existing.level}', workbook: '{t.level}')"
                )
            if program is not None and existing.program_id != program.id:
                conflicts.append(
                    f"program_id (DB: '{existing.program_id}')"
                )
            # Department is part of identity, but already matched in query
            # If department differed, existing would be None

            if conflicts:
                row_errors.append(
                    f"{t.row_ref}: Teacher '{t.acronym}' in department '{t.department}' "
                    f"already exists in DB with conflicting identity fields: {'; '.join(conflicts)}. "
                    "Correct the workbook or DB before importing."
                )
                action = "CONFLICT"
            else:
                action = "REUSE"
                # Normal reuse of a matching teacher — no advisory needed.

        errors.extend(row_errors)
        updated_teachers.append(
            t.model_copy(
                update={
                    "action": action,
                    "resolved_teacher_id": resolved_id,
                    "warnings": row_warnings,
                }
            )
        )

    # -------------------------------------------------------------------- #
    # Propagate CONFLICT errors to affected schedule rows                   #
    # -------------------------------------------------------------------- #
    # Build set of conflicted (acronym, department) pairs
    conflict_identities: set[tuple[str, str]] = {
        (t.acronym, t.department.upper()) for t in updated_teachers if t.action == "CONFLICT"
    }
    if conflict_identities:
        for day_rows in preview.days.values():
            for row in day_rows:
                # Find the teacher for this schedule row
                teacher = next(
                    (t for t in updated_teachers if t.acronym == row.teacher_acronym),
                    None
                )
                if teacher and (teacher.acronym, teacher.department.upper()) in conflict_identities:
                    errors.append(
                        f"{row.row_refs[0]}: Cannot import — teacher "
                        f"'{row.teacher_acronym}' (department '{teacher.department}') has a conflicting DB identity."
                    )

    return preview.model_copy(
        update={
            "teachers": updated_teachers,
            "errors": errors,
        }
    )
