"""
excel_import.persister
----------------------
Transactional persistence layer for confirmed imports.

Rules enforced here:
  - Writes faculty timetables as DRAFT; the caller promotes them to CONFIRMED
    after resource-conflict checks (same path as DOCX).
  - Replaces an existing DRAFT, or an existing IMPORT-sourced CONFIRMED
    timetable, for the same teacher and year. MANUAL CONFIRMED rows are left
    untouched.
  - Runs full TimetableWriteIn Pydantic validation before any DB writes.
  - Automatically populates resources from room values and links schedule_entries.
  - Implements safe resource allocation replacement for re-imports.
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
    ResourceAllocation,
    ResourceAllocationSlot,
    ScheduleEntry,
    ScheduleEntrySlot,
    Teacher,
    TimeSlot,
    Timetable,
)
from app.schemas.imports import ImportPreview
from app.schemas.timetable import ScheduleEntryIn, TimetableWriteIn
from app.services.resource_linking import (
    link_allocation_resources,
    link_schedule_entry_resources,
)


class PersistenceError(Exception):
    """Raised for known, user-facing persistence failures."""


def persist_import(preview: ImportPreview, db: Session) -> dict[str, Any]:
    """
    Write all teachers and DRAFT timetables from *preview* to the database.

    Faculty timetables are stored as DRAFT so the caller can conflict-check
    them before promoting to CONFIRMED. Re-import replaces a previous
    IMPORT-sourced CONFIRMED timetable for the same teacher and year.

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
        if t.is_external:
            continue

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

    # Build a set of (academic_year, day, section, group, slot codes) from current import
    # This defines the "replacement identity" for superseding old allocations
    current_allocation_keys = set()

    for acronym, days_map in entries_by_teacher.items():
        t_row = next((t for t in preview.teachers if t.acronym == acronym), None)
        is_ext = getattr(t_row, "is_external", False) if t_row else False

        if not acronym or is_ext:
            # External allocations - track their replacement keys
            for day_name, import_rows in days_map.items():
                day_iso = DAY_NAME_TO_ISO[day_name]
                for ir in import_rows:
                    # Key: (academic_year, day, section, group_index, frozenset of slot_codes)
                    slot_set = frozenset(ir.slot_ids)
                    key = (preview.academic_year, day_iso, ir.section, ir.group_index, slot_set)
                    current_allocation_keys.add(key)

    # Delete superseded allocations: same (year, day, section, group, slots) from previous imports
    # This implements safe replacement - only delete allocations that match the current import's blocks
    if current_allocation_keys:
        for key in current_allocation_keys:
            academic_year, day_iso, section, group_index, slot_set = key

            # Find allocations matching this exact block identity
            # We need to check each allocation's slots match our slot_set
            candidate_query = (
                select(ResourceAllocation)
                .where(
                    ResourceAllocation.academic_year == academic_year,
                    ResourceAllocation.day_of_week == day_iso,
                    ResourceAllocation.section == section,
                    ResourceAllocation.group_index == group_index,
                    ResourceAllocation.status == "CONFIRMED",
                    ResourceAllocation.source_import_id.isnot(None),  # Only delete imported allocations
                )
            )

            candidates = db.scalars(candidate_query).all()

            for candidate in candidates:
                # Check if this allocation's slots match our slot_set
                candidate_slots = db.scalars(
                    select(TimeSlot.code)
                    .join(ResourceAllocationSlot, ResourceAllocationSlot.time_slot_id == TimeSlot.id)
                    .where(ResourceAllocationSlot.allocation_id == candidate.id)
                ).all()

                candidate_slot_set = frozenset(candidate_slots)

                if candidate_slot_set == slot_set:
                    # This is a superseded allocation - delete it
                    db.delete(candidate)

    # Also delete any allocations from the exact same import session (stale preview data)
    stale_allocs = db.scalars(
        select(ResourceAllocation).where(
            ResourceAllocation.source_import_id == preview.import_id
        )
    ).all()
    for sa in stale_allocs:
        db.delete(sa)

    for acronym, days_map in entries_by_teacher.items():
        t_row = next((t for t in preview.teachers if t.acronym == acronym), None)
        is_ext = getattr(t_row, "is_external", False) if t_row else False

        if not acronym or is_ext:
            # External unowned resource allocations (e.g. Ind*)
            # CRITICAL FIX: Persist as DRAFT, not CONFIRMED, to avoid N+1 conflict validation
            # during initial import. Conflicts will be validated during DRAFT→CONFIRMED promotion.

            for day_name, import_rows in days_map.items():
                day_iso = DAY_NAME_TO_ISO[day_name]
                for ir in import_rows:
                    allocation = ResourceAllocation(
                        id=uuid.uuid4(),
                        academic_year=preview.academic_year,
                        status="DRAFT",  # FIXED: was "CONFIRMED"
                        source_import_id=preview.import_id,
                        day_of_week=day_iso,
                        subject_or_activity=ir.subject_or_activity,
                        section=ir.section,
                        notes=ir.notes,
                        group_index=ir.group_index,
                        source_cell_text=ir.source_cell_text,
                    )
                    db.add(allocation)

                    # Handle slots
                    for slot_code in ir.slot_ids:
                        ts_id = slot_code_to_id.get(slot_code)
                        if not ts_id:
                            raise PersistenceError(f"Time slot {slot_code} not found.")
                        db.add(ResourceAllocationSlot(
                            allocation_id=allocation.id,
                            time_slot_id=ts_id
                        ))

                    stats = link_allocation_resources(
                        db,
                        allocation,
                        ir.resource_codes,
                        ir.room,
                        resource_cache,
                    )
                    resources_created_count += stats.created
                    resources_reused_count += stats.reused
                    entries_linked_count += stats.linked

                    # NO conflict validation here - validation happens during DRAFT→CONFIRMED promotion
                    # This eliminates N+1 queries during import

            continue

        teacher_id = acronym_to_teacher_id.get(acronym)
        if teacher_id is None:
            raise PersistenceError(
                f"Teacher acronym '{acronym}' has schedule rows but was not resolved "
                "to a teacher ID.  This is a logic error."
            )

        # Replace an existing DRAFT, or a previous IMPORT-sourced CONFIRMED
        # timetable, so confirm can promote the new draft without violating
        # one-CONFIRMED-per-teacher-year. MANUAL CONFIRMED rows are not deleted.
        existing_draft: Timetable | None = db.scalar(
            select(Timetable).where(
                Timetable.teacher_id == teacher_id,
                Timetable.academic_year == preview.academic_year,
                Timetable.status == "DRAFT",
            )
        )
        existing_import: Timetable | None = db.scalar(
            select(Timetable).where(
                Timetable.teacher_id == teacher_id,
                Timetable.academic_year == preview.academic_year,
                Timetable.status == "CONFIRMED",
                Timetable.source == "IMPORT",
            )
        )
        replaced = existing_draft is not None or existing_import is not None
        if existing_draft is not None:
            db.delete(existing_draft)
        if existing_import is not None:
            db.delete(existing_import)
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

                stats = link_schedule_entry_resources(
                    db,
                    new_entry,
                    entry_in.resource_codes,
                    entry_in.room,
                    resource_cache,
                )
                resources_created_count += stats.created
                resources_reused_count += stats.reused
                entries_linked_count += stats.linked

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

        # Flush schedule entries to DB to get their IDs before inserting slot links
        db.flush()

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
