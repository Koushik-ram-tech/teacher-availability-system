"""Availability API endpoints.

Provides teacher and resource FREE/BUSY status derived from confirmed timetable data.
"""

from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.availability import AvailabilityService


router = APIRouter(prefix="/availability", tags=["availability"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SlotAvailabilityOut(BaseModel):
    """Availability status for a single slot."""
    slot_code: str
    status: str  # "FREE" | "OCCUPIED" | "UNKNOWN"

    # Optional supporting metadata
    subject_or_activity: Optional[str] = None
    section: Optional[str] = None
    room: Optional[str] = None

    class Config:
        from_attributes = True


class DayAvailabilityOut(BaseModel):
    """Availability for all slots in a day."""
    day: str
    slots: dict[str, SlotAvailabilityOut]


class TeacherAvailabilityOut(BaseModel):
    """Teacher weekly or daily availability."""
    teacher: dict  # {id, name, acronym}
    academic_year: str
    days: dict[str, DayAvailabilityOut]


class ResourceAvailabilityOut(BaseModel):
    """Resource weekly or daily availability."""
    resource: dict  # {id, code, name}
    academic_year: str
    days: dict[str, DayAvailabilityOut]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/teachers/{teacher_id}", response_model=TeacherAvailabilityOut)
def get_teacher_availability(
    teacher_id: UUID,
    academic_year: str = Query(..., description="Academic year (e.g., '2026-2027')"),
    day: Optional[str] = Query(None, description="Optional ISO day name (e.g., 'monday')"),
    db: Session = Depends(get_db)
):
    """Get teacher availability (FREE/BUSY status) for week or specific day.

    Returns working slot availability (Monday-Saturday, S1-S9 excluding breaks).

    Args:
        teacher_id: Teacher UUID
        academic_year: Academic year
        day: Optional specific day (returns full week if omitted)
        db: Database session

    Returns:
        Teacher availability with FREE/OCCUPIED status for each slot

    Raises:
        404: Teacher not found
    """
    # Validate day if provided
    if day:
        valid_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
        if day.lower() not in valid_days:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid day. Must be one of: {', '.join(valid_days)}"
            )
        day = day.lower()

    # Get availability
    availability = AvailabilityService.get_teacher_availability(
        db=db,
        teacher_id=teacher_id,
        academic_year=academic_year,
        day=day
    )

    if not availability:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher not found"
        )

    # Convert to response format
    days_out = {}
    for day_name, day_avail in availability.days.items():
        slots_out = {}
        for slot_code, slot_avail in day_avail.slots.items():
            slots_out[slot_code] = SlotAvailabilityOut(
                slot_code=slot_avail.slot_code,
                status=slot_avail.status,
                subject_or_activity=slot_avail.subject_or_activity,
                section=slot_avail.section,
                room=slot_avail.room
            )
        days_out[day_name] = DayAvailabilityOut(
            day=day_name,
            slots=slots_out
        )

    return TeacherAvailabilityOut(
        teacher={
            "id": str(availability.teacher_id),
            "name": availability.teacher_name,
            "acronym": availability.teacher_acronym
        },
        academic_year=availability.academic_year,
        days=days_out
    )


@router.get("/resources/{resource_id}", response_model=ResourceAvailabilityOut)
def get_resource_availability(
    resource_id: UUID,
    academic_year: str = Query(..., description="Academic year (e.g., '2026-2027')"),
    day: Optional[str] = Query(None, description="Optional ISO day name (e.g., 'monday')"),
    db: Session = Depends(get_db)
):
    """Get resource availability (FREE/BUSY status) for week or specific day.

    Returns working slot availability (Monday-Saturday, S1-S9 excluding breaks).

    Args:
        resource_id: Resource UUID
        academic_year: Academic year
        day: Optional specific day (returns full week if omitted)
        db: Database session

    Returns:
        Resource availability with FREE/OCCUPIED status for each slot

    Raises:
        404: Resource not found
    """
    # Validate day if provided
    if day:
        valid_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
        if day.lower() not in valid_days:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid day. Must be one of: {', '.join(valid_days)}"
            )
        day = day.lower()

    # Get availability
    availability = AvailabilityService.get_resource_availability(
        db=db,
        resource_id=resource_id,
        academic_year=academic_year,
        day=day
    )

    if not availability:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )

    # Convert to response format
    days_out = {}
    for day_name, day_avail in availability.days.items():
        slots_out = {}
        for slot_code, slot_avail in day_avail.slots.items():
            slots_out[slot_code] = SlotAvailabilityOut(
                slot_code=slot_avail.slot_code,
                status=slot_avail.status,
                subject_or_activity=slot_avail.subject_or_activity,
                section=slot_avail.section,
                room=slot_avail.room
            )
        days_out[day_name] = DayAvailabilityOut(
            day=day_name,
            slots=slots_out
        )

    return ResourceAvailabilityOut(
        resource={
            "id": str(availability.resource_id),
            "code": availability.resource_code,
            "name": availability.resource_name
        },
        academic_year=availability.academic_year,
        days=days_out
    )


@router.get("/resources/by-code/{code}", response_model=ResourceAvailabilityOut)
def get_resource_availability_by_code(
    code: str,
    academic_year: str = Query(..., description="Academic year (e.g., '2026-2027')"),
    day: Optional[str] = Query(None, description="Optional ISO day name (e.g., 'monday')"),
    db: Session = Depends(get_db)
):
    """Get resource availability by code or alias.

    Respects resource normalization: "LAB1A" and "LAB 1A" resolve to same resource.

    Args:
        code: Resource code or alias
        academic_year: Academic year
        day: Optional specific day
        db: Database session

    Returns:
        Resource availability

    Raises:
        404: Resource not found
    """
    # Find resource by code
    resource = AvailabilityService.find_resource_by_code(db=db, code=code)

    if not resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource '{code}' not found"
        )

    # Delegate to main endpoint
    return get_resource_availability(
        resource_id=resource.id,
        academic_year=academic_year,
        day=day,
        db=db
    )
