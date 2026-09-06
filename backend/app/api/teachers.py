from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models.models import Program, Teacher
from app.schemas.teacher import ProgramOut, TeacherCreate, TeacherOut, TeacherUpdate

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
        .order_by(Teacher.name)
        .limit(25)
    )
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
