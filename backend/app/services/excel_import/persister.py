"""
excel_import.persister
----------------------
Transactional persistence layer for confirmed imports.

Rules enforced here:
  - NEVER creates a CONFIRMED timetable.
  - NEVER touches an existing CONFIRMED timetable.
  - Deletes and replaces the DRAFT timetable if one already exists.
  - Runs full TimetableWriteIn Pydantic validation before any DB writes.
  - Automatically populates resources from room values and links schedule_entries.
  - The caller (API layer) is responsible for commit/rollback; this module
    only flushes within the session so all writes are in one transaction.

If any step raises, the caller must call db.rollback() to undo all flushed
writes.  A PersistenceError is raised for known domain failures.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.schedule_config import DAY_NAME_TO_ISO
from app.models.models import (
    Program,
    Resource,
    ScheduleEntry,
    ScheduleEntryResource,
    ScheduleEntrySlot,
    Teacher,
    TimeSlot,
    Timetable,
)
from app.schemas.imports import ImportPreview
from app.schemas.timetable import ScheduleEntryIn, TimetableWriteIn
from app.services.resource_populator import (
    classify_resource_type,
    find_or_create_resource,
    create_alias_if_needed,
)


class PersistenceError(Exception):
    """Raised for known, user-facing persistence failures."""


def persist_import(preview: ImportPreview, db: Session) -> dict[str, Any]:
    """
    Write all teachers and DRAFT timetables from *preview* to the database.

    Automatically populates resources from room values and links schedule_entries.

    The caller must call ``db.commit()`` on success or ``db.rollback()`` on
    failure.  This function only calls ``db.flush()`` to obtain generated IDs.

    Returns::

        {
            "teachers_created":    [str, ...],  # new teacher UUIDs
            "teachers_reused":     [str, ...],  # existing teacher UUIDs
            "timetables_created":  [str, ...],  # new timetable UUIDs
            "timetables_replaced": [str, ...],  # replaced DRAFT timetable UUIDs
            "resources_created":   int,          # new resources created
            "resources_reused":    int,          # existing resources reused
            "entries_linked":      int,          # schedule_entries with resource_id set
        }
    """
    teachers_created: list[str] = []
    teachers_reused: list[str] = []
    timetables_created: list[str] = []
    timetables_replaced: list[str] = []
    resources_created_count = 0
    resources_reused_count = 0
    entries_linked_count = 0

    # Resource cache: {normalized_name: Resource}
    # Prevents duplicate lookups/creations within same transaction
    resource_cache: dict[str, Resource] = {}

    # ---------------------------------------------------------------------- #
    # Resolve time slots once (code → UUID)                                   #
    # ---------------------------------------------------------------------- #
    slot_rows = db.scalars(
        select(TimeSlot).where(TimeSlot.is_active.is_(True))
    ).all()
    slot_code_to_id: dict[str, uuid.UUID] = {s.code: s.id for s in slot_rows}

    if not slot_code_to_id:
        raise PersistenceError(
            "No active time slots found in the database. "
            "Seed the time_slots table before importing."
        )

    # ---------------------------------------------------------------------- #
    # Step 1: Resolve / create teachers                                       #
    # ---------------------------------------------------------------------- #
    acronym_to_teacher_id: dict[str, uuid.UUID] = {}

    for t in preview.teachers:
        if t.action == "REUSE":
            if t.resolved_teacher_id is None:
                raise PersistenceError(
                    f"Teacher '{t.acronym}' marked REUSE but has no resolved_teacher_id."
                )
            acronym_to_teacher_id[t.acronym] = t.resolved_teacher_id
            teachers_reused.append(str(t.resolved_teacher_id))
            continue

        if t.action == "CONFLICT":
            raise PersistenceError(
                f"Cannot persist: teacher '{t.acronym}' has a CONFLICT action. "
                "Validation should have blocked confirmation."
            )

        # CREATE
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
            raise PersistenceError(
                f"Program '{t.program_name}' level '{t.level}' not found at persist time."
            )

        new_teacher = Teacher(
            id=uuid.uuid4(),
            name=t.name,
            acronym=t.acronym,
            level=t.level,
            program_id=program.id,
            semester=t.semester,
            department=t.department,
            is_active=True,
        )
        db.add(new_teacher)
        db.flush()
        acronym_to_teacher_id[t.acronym] = new_teacher.id
        teachers_created.append(str(new_teacher.id))

    # ---------------------------------------------------------------------- #
    # Step 2: Group schedule rows by teacher acronym                          #
    # ---------------------------------------------------------------------- #
    # {acronym: {day_name: [ScheduleImportRow, ...]}}
    entries_by_teacher: dict[str, dict[str, list]] = {}
    for day_name, rows in preview.days.items():
        for row in rows:
            by_day = entries_by_teacher.setdefault(row.teacher_acronym, {})
            by_day.setdefault(day_name, []).append(row)

    # ---------------------------------------------------------------------- #
    # Step 3: Create/replace DRAFT timetable per teacher                      #
    # ---------------------------------------------------------------------- #

    from app.models.models import ResourceAllocation, ResourceAllocationSlot, ResourceAllocationResource
    from app.domain.resources import normalize_resource_name
    from app.services.availability import check_resource_conflicts
    import re

    # Delete existing allocations from a previous run of this exact import session
    existing_allocs = db.scalars(
        select(ResourceAllocation).where(
            ResourceAllocation.source_import_id == preview.import_id
        )
    ).all()
    for ea in existing_allocs:
        db.delete(ea)
    db.flush()

    for acronym, days_map in entries_by_teacher.items():
        if not acronym:
            # External unowned resource allocations (e.g. Ind*)

            for day_name, import_rows in days_map.items():
                day_iso = DAY_NAME_TO_ISO[day_name]
                for ir in import_rows:
                    allocation = ResourceAllocation(
                        id=uuid.uuid4(),
                        academic_year=preview.academic_year,
                        status="CONFIRMED",
                        source_import_id=preview.import_id,
                        day_of_week=day_iso,
                        subject_or_activity=ir.subject_or_activity,
                        section=ir.section,
                        notes=ir.notes,
                        source_cell_text=ir.source_cell_text,
                    )
                    db.add(allocation)
                    db.flush()

                    # Handle slots
                    for slot_code in ir.slot_ids:
                        ts_id = slot_code_to_id.get(slot_code)
                        if not ts_id:
                            raise PersistenceError(f"Time slot {slot_code} not found.")
                        db.add(ResourceAllocationSlot(
                            allocation_id=allocation.id,
                            time_slot_id=ts_id
                        ))

                    # Handle resources
                    codes_to_link: list[str] = []
                    if ir.resource_codes:
                        codes_to_link = [c.strip() for c in ir.resource_codes if c.strip()]
                    elif ir.room and ir.room.strip():
                        codes_to_link = [ir.room.strip()]

                    for code in codes_to_link:
                        normalized = normalize_resource_name(code)
                        if normalized in resource_cache:
                            resource = resource_cache[normalized]
                        else:
                            resource_type = classify_resource_type(code)
                            resource = find_or_create_resource(db=db, name=code, resource_type=resource_type, department=None)
                            resource_cache[normalized] = resource
                            # Create common aliases
                            if "lab" in code.lower():
                                if " " not in code:
                                    spaced = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', code)
                                    if spaced != code:
                                        create_alias_if_needed(db, resource, spaced)
                                else:
                                    no_space = code.replace(" ", "")
                                    if no_space != code:
                                        create_alias_if_needed(db, resource, no_space)

                        db.add(ResourceAllocationResource(
                            allocation_id=allocation.id,
                            resource_id=resource.id
                        ))

                    # Validate resource conflicts before allowing CONFIRMED persistence
                    if codes_to_link:
                        slot_ids_for_check = [slot_code_to_id[code] for code in ir.slot_ids]
                        resource_ids_for_check = [
                            resource_cache[normalize_resource_name(code)].id
                            for code in codes_to_link
                        ]
                        has_conflict, error_msg = check_resource_conflicts(
                            db=db,
                            academic_year=preview.academic_year,
                            resource_ids=resource_ids_for_check,
                            day_of_week=day_iso,
                            time_slot_ids=slot_ids_for_check
                        )
                        if has_conflict:
                            raise PersistenceError(
                                f"Cannot confirm external allocation for '{ir.subject_or_activity}': {error_msg}"
                            )

            continue

        teacher_id = acronym_to_teacher_id.get(acronym)
        if teacher_id is None:
            raise PersistenceError(
                f"Teacher acronym '{acronym}' has schedule rows but was not resolved "
                "to a teacher ID.  This is a logic error."
            )

        # Ensure we never touch a CONFIRMED timetable
        # (we only create/replace DRAFT)

        # Delete existing DRAFT timetable if present (cascade removes entries+slots)
        existing_draft: Timetable | None = db.scalar(
            select(Timetable).where(
                Timetable.teacher_id == teacher_id,
                Timetable.academic_year == preview.academic_year,
                Timetable.status == "DRAFT",
            )
        )
        replaced = existing_draft is not None
        if existing_draft is not None:
            db.delete(existing_draft)
            db.flush()

        # Build TimetableWriteIn payload for full Pydantic validation
        # (slot code validity, LAB rules, no duplicate slots per day, etc.)
        days_payload: dict[str, list[ScheduleEntryIn]] = {}
        for day_name, import_rows in days_map.items():
            entry_ins: list[ScheduleEntryIn] = []
            for ir in import_rows:
                entry_ins.append(
                    ScheduleEntryIn(
                        slot_ids=ir.slot_ids,
                        entry_type=ir.entry_type,  # type: ignore[arg-type]
                        subject_or_activity=ir.subject_or_activity,
                        section=ir.section,
                        room=ir.room,
                        resource_codes=list(ir.resource_codes),
                        notes=ir.notes,
                        group_index=ir.group_index,
                        source_cell_text=ir.source_cell_text,
                    )
                )
            days_payload[day_name] = entry_ins

        try:
            validated = TimetableWriteIn(
                academic_year=preview.academic_year,
                days=days_payload,
            )
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"Timetable schema validation failed for teacher '{acronym}': {exc}"
            ) from exc

        # Create new DRAFT timetable row (source = IMPORT)
        new_timetable = Timetable(
            id=uuid.uuid4(),
            teacher_id=teacher_id,
            academic_year=preview.academic_year,
            status="DRAFT",
            source="IMPORT",
        )
        db.add(new_timetable)
        db.flush()

        # Persist schedule entries and slot links
        # Collect all slot objects for bulk insert at the end
        slot_objects: list[ScheduleEntrySlot] = []

        for day_name, entries_in in validated.days.items():
            day_iso = DAY_NAME_TO_ISO[day_name]
            for entry_in in entries_in:
                new_entry = ScheduleEntry(
                    id=uuid.uuid4(),
                    timetable_id=new_timetable.id,
                    day_of_week=day_iso,
                    entry_type=entry_in.entry_type,
                    subject_or_activity=entry_in.effective_subject,
                    section=entry_in.section,
                    room=entry_in.room,
                    notes=entry_in.notes,
                    group_index=entry_in.group_index,
                    source_cell_text=entry_in.source_cell_text,
                )
                db.add(new_entry)
                db.flush()

                # --------------------------------------------------
                # Resource Population Integration
                # --------------------------------------------------
                # Determine the list of individual resource codes to link:
                #   - Use entry_in.resource_codes (authoritative, from parser)
                #   - Fall back to entry_in.room as a single code if resource_codes is empty
                #     (maintains backwards compat for manual entry and XLSX import)
                from app.domain.resources import normalize_resource_name
                import re

                codes_to_link: list[str] = []
                if entry_in.resource_codes:
                    codes_to_link = [c.strip() for c in entry_in.resource_codes if c.strip()]
                elif entry_in.room and entry_in.room.strip():
                    # Legacy fallback: treat room string as a single resource code
                    codes_to_link = [entry_in.room.strip()]

                for code in codes_to_link:
                    normalized = normalize_resource_name(code)

                    if normalized in resource_cache:
                        resource = resource_cache[normalized]
                        resources_reused_count += 1
                    else:
                        # Check if resource already exists in DB
                        existing_count = db.execute(
                            select(func.count(Resource.id))
                            .where(Resource.normalized_name == normalized)
                            .where(Resource.department.is_(None))
                        ).scalar()

                        resource_type = classify_resource_type(code)
                        resource = find_or_create_resource(
                            db=db,
                            name=code,
                            resource_type=resource_type,
                            department=None,  # Shared resources
                        )

                        if existing_count == 0:
                            resources_created_count += 1
                        else:
                            resources_reused_count += 1

                        resource_cache[normalized] = resource

                        # Create common aliases (Lab1A <-> Lab 1A)
                        if "lab" in code.lower():
                            if " " not in code:
                                spaced = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', code)
                                if spaced != code:
                                    create_alias_if_needed(db, resource, spaced)
                            else:
                                no_space = code.replace(" ", "")
                                if no_space != code:
                                    create_alias_if_needed(db, resource, no_space)

                    # Link via many-to-many join table (authoritative for multi-resource)
                    db.add(ScheduleEntryResource(
                        schedule_entry_id=new_entry.id,
                        resource_id=resource.id,
                    ))

                    # Also set singular resource_id to first resource for backwards compat
                    # (used by manual-entry availability queries that haven't migrated yet)
                    if new_entry.resource_id is None:
                        new_entry.resource_id = resource.id

                if codes_to_link:
                    entries_linked_count += len(codes_to_link)

                # Collect slot links for bulk insert
                for code in entry_in.slot_ids:
                    ts_id = slot_code_to_id.get(code)
                    if ts_id is None:
                        raise PersistenceError(
                            f"Time slot '{code}' not found in DB "
                            f"(active time_slots table)."
                        )
                    slot_objects.append(
                        ScheduleEntrySlot(
                            schedule_entry_id=new_entry.id,
                            time_slot_id=ts_id,
                        )
                    )

        # Bulk insert all slot links for this timetable
        if slot_objects:
            db.bulk_save_objects(slot_objects)

        if replaced:
            timetables_replaced.append(str(new_timetable.id))
        else:
            timetables_created.append(str(new_timetable.id))

    # Final flush before caller commits
    db.flush()

    return {
        "teachers_created": teachers_created,
        "teachers_reused": teachers_reused,
        "timetables_created": timetables_created,
        "timetables_replaced": timetables_replaced,
        "resources_created": resources_created_count,
        "resources_reused": resources_reused_count,
        "entries_linked": entries_linked_count,
    }
