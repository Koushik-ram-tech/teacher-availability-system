from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.schedule_config import DAY_NAME_TO_ISO, ISO_TO_DAY_NAME, PERIOD_SEQUENCE
from app.db import get_db
from app.models.models import Program, ScheduleEntry, ScheduleEntrySlot, Teacher, TimeSlot, Timetable
from app.schemas.teacher import ProgramOut, TeacherCreate, TeacherOut, TeacherUpdate
from app.schemas.timetable import (
    DayPeriodOut,
    ScheduleEntryOut,
    TimetableConfirmOut,
    TimetableDayOut,
    TimetableWriteIn,
    TimetableWriteOut,
)

router = APIRouter(prefix="/teachers", tags=["teachers"])


def _normalize_acronym(value: str) -> str:
    return " ".join(value.strip().split()).upper()


def _get_constraint_name(exc: IntegrityError) -> str:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", "") or ""


def _get_teacher(db: Session, teacher_id: UUID) -> Teacher:
    teacher = db.scalar(
        select(Teacher)
        .options(selectinload(Teacher.program))
        .where(Teacher.id == teacher_id)
    )
    if teacher is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Teacher not found")
    return teacher


# ---------------------------------------------------------------------------
# Teacher CRUD
# ---------------------------------------------------------------------------


@router.get("/programs", response_model=list[ProgramOut])
def list_programs(db: Session = Depends(get_db)) -> list[Program]:
    return list(
        db.scalars(
            select(Program).where(Program.is_active.is_(True)).order_by(Program.level, Program.name)
        ).all()
    )


@router.post("", response_model=TeacherOut, status_code=status.HTTP_201_CREATED)
def create_teacher(payload: TeacherCreate, db: Session = Depends(get_db)) -> Teacher:
    teacher = Teacher(
        name=payload.name.strip(),
        acronym=_normalize_acronym(payload.acronym),
        level=payload.level,
        program_id=payload.program_id,
        semester=payload.semester,
        department=payload.department.strip(),
    )
    db.add(teacher)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        constraint = _get_constraint_name(exc)
        # Department-aware uniqueness: same acronym allowed in different departments
        if constraint == "uq_teachers_acronym_department_normalized":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A teacher with acronym '{payload.acronym}' already exists in department '{payload.department}'",
            ) from exc
        # Legacy constraint name (for backward compatibility during migration)
        if constraint == "uq_teachers_acronym_normalized":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A teacher with this acronym already exists",
            ) from exc
        if constraint == "teachers_program_level_fk":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Teacher level does not match the selected program",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Teacher data violates a database constraint",
        ) from exc

    return _get_teacher(db, teacher.id)


@router.get("", response_model=list[TeacherOut])
def list_teachers(db: Session = Depends(get_db)) -> list[Teacher]:
    return list(
        db.scalars(
            select(Teacher)
            .options(selectinload(Teacher.program))
            .where(Teacher.is_active.is_(True))
            .order_by(Teacher.name)
        ).all()
    )


@router.get("/search", response_model=list[TeacherOut])
def search_teachers(
    q: str = Query(min_length=1, max_length=100),
    department: str | None = Query(None, description="Optional department filter"),
    db: Session = Depends(get_db),
) -> list[Teacher]:
    term = q.strip()
    if not term:
        return []
    pattern = f"%{term}%"
    stmt = (
        select(Teacher)
        .options(selectinload(Teacher.program))
        .where(
            Teacher.is_active.is_(True),
            (Teacher.name.ilike(pattern) | Teacher.acronym.ilike(pattern)),
        )
    )
    # Optional department filter
    if department:
        dept = department.strip()
        stmt = stmt.where(Teacher.department.ilike(dept))
    
    stmt = stmt.order_by(Teacher.name).limit(25)
    return list(db.scalars(stmt).all())


@router.get("/{teacher_id}", response_model=TeacherOut)
def get_teacher(teacher_id: UUID, db: Session = Depends(get_db)) -> Teacher:
    return _get_teacher(db, teacher_id)


@router.put("/{teacher_id}", response_model=TeacherOut)
def update_teacher(
    teacher_id: UUID,
    payload: TeacherUpdate,
    db: Session = Depends(get_db),
) -> Teacher:
    teacher = _get_teacher(db, teacher_id)
    teacher.name = payload.name.strip()
    teacher.acronym = _normalize_acronym(payload.acronym)
    teacher.level = payload.level
    teacher.program_id = payload.program_id
    teacher.semester = payload.semester
    teacher.department = payload.department.strip()
    teacher.is_active = payload.is_active

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Updated teacher data conflicts with existing data",
        ) from exc

    return _get_teacher(db, teacher_id)


# ---------------------------------------------------------------------------
# Timetable helpers
# ---------------------------------------------------------------------------


def _get_slot_code_to_uuid(db: Session) -> dict[str, UUID]:
    """Return a mapping of slot code -> UUID from the time_slots table."""
    rows = db.scalars(select(TimeSlot).where(TimeSlot.is_active.is_(True))).all()
    return {row.code: row.id for row in rows}


def _get_slot_uuid_to_code(db: Session) -> dict[UUID, str]:
    rows = db.scalars(select(TimeSlot).where(TimeSlot.is_active.is_(True))).all()
    return {row.id: row.code for row in rows}


def _get_exact_timetable(
    db: Session,
    teacher_id: UUID,
    academic_year: str,
    timetable_status: str,
) -> Timetable | None:
    """Fetch the exact timetable by (teacher_id, academic_year, status).

    Returns None if it does not exist.  No fallback, no inference.
    """
    return db.scalar(
        select(Timetable)
        .options(
            selectinload(Timetable.entries).selectinload(ScheduleEntry.slot_links).selectinload(
                ScheduleEntrySlot.time_slot
            )
        )
        .where(
            Timetable.teacher_id == teacher_id,
            Timetable.academic_year == academic_year,
            Timetable.status == timetable_status,
        )
    )


def _build_day_response(
    timetable: Timetable | None,
    teacher_id: UUID,
    academic_year: str,
    day_name: str,
    timetable_status: str,
    uuid_to_code: dict[UUID, str],
) -> TimetableDayOut:
    """Build a TimetableDayOut for the given day."""
    day_iso = DAY_NAME_TO_ISO[day_name]

    # Build entry lookup: slot_code -> ScheduleEntryOut for this day.
    slot_code_to_entry: dict[str, ScheduleEntryOut] = {}
    if timetable:
        for entry in timetable.entries:
            if entry.day_of_week != day_iso:
                continue
            codes = [
                uuid_to_code[link.time_slot_id]
                for link in entry.slot_links
                if link.time_slot_id in uuid_to_code
            ]
            entry_out = ScheduleEntryOut(
                id=entry.id,
                entry_type=entry.entry_type,  # type: ignore[arg-type]
                subject_or_activity=entry.subject_or_activity,
                section=entry.section,
                room=entry.room,
                notes=entry.notes,
                slot_codes=sorted(codes),
            )
            for code in codes:
                slot_code_to_entry[code] = entry_out

    periods: list[DayPeriodOut] = []
    for period_cfg in PERIOD_SEQUENCE:
        if period_cfg.kind == "BREAK":
            periods.append(
                DayPeriodOut(
                    kind="BREAK",
                    label=period_cfg.label,
                    start_time=period_cfg.start_time,
                    end_time=period_cfg.end_time,
                    entry=None,
                )
            )
        else:
            assert period_cfg.code is not None
            periods.append(
                DayPeriodOut(
                    kind="SLOT",
                    code=period_cfg.code,
                    start_time=period_cfg.start_time,
                    end_time=period_cfg.end_time,
                    entry=slot_code_to_entry.get(period_cfg.code),
                )
            )

    return TimetableDayOut(
        teacher_id=teacher_id,
        academic_year=academic_year,
        day=day_name,
        timetable_status=timetable_status,  # type: ignore[arg-type]
        periods=periods,
    )


def _build_write_out(
    timetable: Timetable,
    uuid_to_code: dict[UUID, str],
) -> TimetableWriteOut:
    """Collapse the timetable ORM object into a TimetableWriteOut response."""
    days: dict[str, list[ScheduleEntryOut]] = {}
    for entry in timetable.entries:
        day_name = ISO_TO_DAY_NAME.get(entry.day_of_week)
        if day_name is None:
            continue
        codes = sorted(
            uuid_to_code[link.time_slot_id]
            for link in entry.slot_links
            if link.time_slot_id in uuid_to_code
        )
        entry_out = ScheduleEntryOut(
            id=entry.id,
            entry_type=entry.entry_type,  # type: ignore[arg-type]
            subject_or_activity=entry.subject_or_activity,
            section=entry.section,
            room=entry.room,
            notes=entry.notes,
            slot_codes=codes,
        )
        days.setdefault(day_name, []).append(entry_out)

    return TimetableWriteOut(
        id=timetable.id,
        teacher_id=timetable.teacher_id,
        academic_year=timetable.academic_year,
        status=timetable.status,  # type: ignore[arg-type]
        source=timetable.source,  # type: ignore[arg-type]
        days=days,
    )


# ---------------------------------------------------------------------------
# Timetable endpoints
# ---------------------------------------------------------------------------


@router.get("/{teacher_id}/timetable", response_model=TimetableDayOut)
def get_timetable_day(
    teacher_id: UUID,
    day: str = Query(description="Lowercase day name, e.g. monday"),
    academic_year: str = Query(description="Academic year string, e.g. 2025-2026"),
    status: str = Query(description="DRAFT or CONFIRMED"),
    db: Session = Depends(get_db),
) -> TimetableDayOut:
    """Return the exact requested timetable day.

    Resolution is by (teacher_id, academic_year, status) only - no fallback,
    no inference, no cross-year lookup.  404 means the exact combination does
    not exist; any other status code is a real error.
    """
    day = day.strip().lower()
    academic_year = academic_year.strip()
    timetable_status = status.strip().upper()

    if day not in DAY_NAME_TO_ISO:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid day name '{day}'. Valid values: {sorted(DAY_NAME_TO_ISO.keys())}",
        )
    if timetable_status not in {"DRAFT", "CONFIRMED"}:
        raise HTTPException(status_code=400, detail="status must be DRAFT or CONFIRMED")
    if not academic_year:
        raise HTTPException(status_code=400, detail="academic_year is required")

    # Verify teacher exists; raises 404 if not.
    _get_teacher(db, teacher_id)

    timetable = _get_exact_timetable(db, teacher_id, academic_year, timetable_status)
    if timetable is None:
        raise HTTPException(
            status_code=404,
            detail=f"No {timetable_status} timetable found for teacher {teacher_id} / academic year '{academic_year}'.",
        )

    uuid_to_code = _get_slot_uuid_to_code(db)
    return _build_day_response(timetable, teacher_id, academic_year, day, timetable_status, uuid_to_code)


@router.post("/{teacher_id}/timetable", response_model=TimetableWriteOut, status_code=status.HTTP_201_CREATED)
def create_timetable(
    teacher_id: UUID,
    payload: TimetableWriteIn,
    db: Session = Depends(get_db),
) -> TimetableWriteOut:
    """Create a new DRAFT timetable for the exact (teacher_id, academic_year).

    If a DRAFT already exists for this teacher+year, returns 409.
    Never creates or modifies a CONFIRMED timetable.
    """
    _get_teacher(db, teacher_id)

    existing_draft = db.scalar(
        select(Timetable).where(
            Timetable.teacher_id == teacher_id,
            Timetable.academic_year == payload.academic_year,
            Timetable.status == "DRAFT",
        )
    )
    if existing_draft is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A DRAFT timetable already exists for teacher {teacher_id} / "
                f"academic year '{payload.academic_year}'. Use PUT to update it."
            ),
        )

    code_to_uuid = _get_slot_code_to_uuid(db)

    timetable = Timetable(
        teacher_id=teacher_id,
        academic_year=payload.academic_year,
        status="DRAFT",
        source="MANUAL",
    )
    db.add(timetable)
    db.flush()  # get timetable.id before adding entries

    for day_name, entries in payload.days.items():
        day_iso = DAY_NAME_TO_ISO[day_name]
        for entry_in in entries:
            entry = ScheduleEntry(
                timetable_id=timetable.id,
                day_of_week=day_iso,
                entry_type=entry_in.entry_type,
                subject_or_activity=entry_in.effective_subject,
                section=entry_in.section,
                room=entry_in.room,
                notes=entry_in.notes,
            )
            db.add(entry)
            db.flush()
            for slot_code in entry_in.slot_ids:
                db.add(ScheduleEntrySlot(
                    schedule_entry_id=entry.id,
                    time_slot_id=code_to_uuid[slot_code],
                ))

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Timetable data violates a database constraint.",
        ) from exc

    # Reload with relationships for response
    timetable_reloaded = _get_exact_timetable(db, teacher_id, payload.academic_year, "DRAFT")
    assert timetable_reloaded is not None
    uuid_to_code = _get_slot_uuid_to_code(db)
    return _build_write_out(timetable_reloaded, uuid_to_code)


@router.put("/{teacher_id}/timetable", response_model=TimetableWriteOut)
def update_timetable(
    teacher_id: UUID,
    payload: TimetableWriteIn,
    db: Session = Depends(get_db),
) -> TimetableWriteOut:
    """Replace the DRAFT timetable for the exact (teacher_id, academic_year).

    If no DRAFT exists for this teacher+year, returns 404.
    All existing entries and slot links for the DRAFT are deleted and
    replaced atomically.  CONFIRMED timetables are never touched.
    Validation runs before any deletion; partial replacement cannot occur.
    """
    _get_teacher(db, teacher_id)

    timetable = db.scalar(
        select(Timetable).where(
            Timetable.teacher_id == teacher_id,
            Timetable.academic_year == payload.academic_year,
            Timetable.status == "DRAFT",
        )
    )
    if timetable is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No DRAFT timetable found for teacher {teacher_id} / "
                f"academic year '{payload.academic_year}'. Use POST to create one."
            ),
        )

    code_to_uuid = _get_slot_code_to_uuid(db)

    # Validate all slot codes before touching the database.
    unknown_codes: set[str] = set()
    for entries in payload.days.values():
        for entry_in in entries:
            for code in entry_in.slot_ids:
                if code not in code_to_uuid:
                    unknown_codes.add(code)
    if unknown_codes:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown slot codes: {sorted(unknown_codes)}",
        )

    # Atomically replace all entries.  Cascade delete removes slot links.
    existing_entries = db.scalars(
        select(ScheduleEntry).where(ScheduleEntry.timetable_id == timetable.id)
    ).all()
    for entry in existing_entries:
        db.delete(entry)
    db.flush()

    for day_name, entries in payload.days.items():
        day_iso = DAY_NAME_TO_ISO[day_name]
        for entry_in in entries:
            entry = ScheduleEntry(
                timetable_id=timetable.id,
                day_of_week=day_iso,
                entry_type=entry_in.entry_type,
                subject_or_activity=entry_in.effective_subject,
                section=entry_in.section,
                room=entry_in.room,
                notes=entry_in.notes,
            )
            db.add(entry)
            db.flush()
            for slot_code in entry_in.slot_ids:
                db.add(ScheduleEntrySlot(
                    schedule_entry_id=entry.id,
                    time_slot_id=code_to_uuid[slot_code],
                ))

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Timetable update violates a database constraint.",
        ) from exc

    timetable_reloaded = _get_exact_timetable(db, teacher_id, payload.academic_year, "DRAFT")
    assert timetable_reloaded is not None
    uuid_to_code = _get_slot_uuid_to_code(db)
    return _build_write_out(timetable_reloaded, uuid_to_code)


# ---------------------------------------------------------------------------
# POST /{teacher_id}/timetable/confirm  — promote DRAFT → CONFIRMED
# ---------------------------------------------------------------------------


@router.post(
    "/{teacher_id}/timetable/confirm",
    response_model=TimetableConfirmOut,
    summary="Promote a DRAFT timetable to CONFIRMED",
)
def confirm_timetable(
    teacher_id: UUID,
    academic_year: str = Query(description="Academic year string, e.g. 2025-2026"),
    db: Session = Depends(get_db),
) -> TimetableConfirmOut:
    """Promote a teacher's DRAFT timetable to CONFIRMED status.

    Safety rules enforced:
    - 404  if no DRAFT timetable exists for (teacher_id, academic_year).
    - 409  if a CONFIRMED timetable already exists for the same pair
           (one CONFIRMED per teacher per year is the database invariant).
    - 422  if the DRAFT contains slot/LAB/duplicate violations detected at
           promotion time (structural re-validation via TimetableWriteIn).
    - 500  on unexpected DB errors; all changes are rolled back.

    On success:
    - status changed from DRAFT to CONFIRMED.
    - last_verified_at stamped with the current UTC timestamp.
    - Returns a TimetableConfirmOut summary.

    CONFIRMED timetables are NEVER modified by this endpoint — only DRAFTs
    are promoted.  The Excel import flow is also DRAFT-only and is unaffected.
    """
    from datetime import datetime, timezone

    academic_year = academic_year.strip()
    if not academic_year:
        raise HTTPException(status_code=400, detail="academic_year is required.")

    # Verify teacher exists.
    _get_teacher(db, teacher_id)

    # Step 1 — Reject immediately if a CONFIRMED timetable already exists.
    existing_confirmed = _get_exact_timetable(db, teacher_id, academic_year, "CONFIRMED")
    if existing_confirmed is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A CONFIRMED timetable already exists for teacher {teacher_id} / "
                f"academic year '{academic_year}'. "
                "Withdraw or delete it before promoting a new draft."
            ),
        )

    # Step 2 — Locate the DRAFT.
    draft = _get_exact_timetable(db, teacher_id, academic_year, "DRAFT")
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No DRAFT timetable found for teacher {teacher_id} / "
                f"academic year '{academic_year}'. "
                "Save a draft first before confirming."
            ),
        )

    # Step 3 — Structural re-validation via TimetableWriteIn Pydantic model.
    # This catches any slot-code, LAB-pair, or duplicate-slot violations that
    # might have been introduced by a direct DB manipulation or schema change.
    uuid_to_code = _get_slot_uuid_to_code(db)
    iso_to_day = {v: k for k, v in DAY_NAME_TO_ISO.items()}

    days_payload: dict[str, list] = {}
    for entry in draft.entries:
        day_name = iso_to_day.get(entry.day_of_week)
        if day_name is None:
            continue
        codes = sorted(
            uuid_to_code[link.time_slot_id]
            for link in entry.slot_links
            if link.time_slot_id in uuid_to_code
        )
        from app.schemas.timetable import ScheduleEntryIn as _SEIn
        days_payload.setdefault(day_name, []).append(
            dict(
                slot_ids=codes,
                entry_type=entry.entry_type,
                subject_or_activity=entry.subject_or_activity,
                section=entry.section,
                room=entry.room,
                notes=entry.notes,
            )
        )

    # Per-entry slot validation: catches unknown slot codes, LAB-pair
    # violations, and duplicate slots within a day.
    from app.schemas.timetable import ScheduleEntryIn
    validation_errors: list[str] = []
    for day_name, raw_entries in days_payload.items():
        seen_codes: set[str] = set()
        for raw in raw_entries:
            try:
                ScheduleEntryIn(**raw)
            except Exception as exc:  # noqa: BLE001
                validation_errors.append(f"Day {day_name}: {exc}")
                continue
            overlap = seen_codes & set(raw["slot_ids"])
            if overlap:
                validation_errors.append(
                    f"Day {day_name}: slots {sorted(overlap)} claimed by more than one entry."
                )
            seen_codes.update(raw["slot_ids"])

    if validation_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "DRAFT timetable has validation errors and cannot be confirmed.",
                "errors": validation_errors,
            },
        )

    # Step 4 — Promote: update status + stamp last_verified_at.
    now_utc = datetime.now(timezone.utc)
    draft.status = "CONFIRMED"  # type: ignore[assignment]
    draft.last_verified_at = now_utc

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Could not confirm timetable due to a database constraint violation. "
                "A CONFIRMED timetable for this teacher/year may already exist."
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during confirmation. All changes rolled back.",
        ) from exc

    # Reload to get the committed state.
    db.refresh(draft)
    entry_count = len(draft.entries)

    return TimetableConfirmOut(
        id=draft.id,
        teacher_id=draft.teacher_id,
        academic_year=draft.academic_year,
        status=draft.status,  # type: ignore[arg-type]
        source=draft.source,  # type: ignore[arg-type]
        confirmed_at=draft.last_verified_at.isoformat(),
        entry_count=entry_count,
    )
