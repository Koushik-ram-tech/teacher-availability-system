"""Availability API endpoints.

Provides teacher and resource FREE/BUSY status derived from confirmed timetable data.
"""

from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.models import (
    Resource,
    ResourceAllocation,
    ResourceAllocationResource,
    ResourceAllocationSlot,
    ScheduleEntry,
    ScheduleEntryResource,
    ScheduleEntrySlot,
    TimeSlot,
    Timetable,
)
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


class ResourceCatalogItem(BaseModel):
    id: UUID
    code: str
    name: str
    resource_type: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/resources/catalog", response_model=list[ResourceCatalogItem])
def get_resource_catalog(
    department: str = Query(..., min_length=1),
    academic_year: str = Query(...),
    day: str | None = Query(None),
    slots: str = Query("", description="Comma-separated slot codes"),
    db: Session = Depends(get_db),
) -> list[ResourceCatalogItem]:
    """List active shared/department resources, excluding slot conflicts."""
    resources = db.scalars(
        select(Resource)
        .where(
            Resource.is_active.is_(True),
            or_(
                Resource.department.is_(None),
                func.lower(func.trim(Resource.department)) == department.strip().lower(),
            ),
        )
        .order_by(Resource.name)
    ).all()

    slot_codes = [code.strip().upper() for code in slots.split(",") if code.strip()]
    occupied_resource_ids: set[UUID] = set()
    if day and slot_codes and resources:
        day_to_iso = {
            "monday": 1,
            "tuesday": 2,
            "wednesday": 3,
            "thursday": 4,
            "friday": 5,
            "saturday": 6,
        }
        day_iso = day.strip().lower()
        if day_iso not in day_to_iso:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid day. Must be Monday through Saturday.",
            )

        resource_ids = [resource.id for resource in resources]
        faculty_conflicts = db.scalars(
            select(ScheduleEntryResource.resource_id)
            .join(ScheduleEntry, ScheduleEntry.id == ScheduleEntryResource.schedule_entry_id)
            .join(Timetable, Timetable.id == ScheduleEntry.timetable_id)
            .join(ScheduleEntrySlot, ScheduleEntrySlot.schedule_entry_id == ScheduleEntry.id)
            .join(TimeSlot, TimeSlot.id == ScheduleEntrySlot.time_slot_id)
            .where(
                Timetable.academic_year == academic_year,
                Timetable.status.in_(["DRAFT", "CONFIRMED"]),
                ScheduleEntry.day_of_week == day_to_iso[day_iso],
                ScheduleEntryResource.resource_id.in_(resource_ids),
                TimeSlot.code.in_(slot_codes),
            )
        ).all()
        allocation_conflicts = db.scalars(
            select(ResourceAllocationResource.resource_id)
            .join(ResourceAllocation, ResourceAllocation.id == ResourceAllocationResource.allocation_id)
            .join(ResourceAllocationSlot, ResourceAllocationSlot.allocation_id == ResourceAllocation.id)
            .join(TimeSlot, TimeSlot.id == ResourceAllocationSlot.time_slot_id)
            .where(
                ResourceAllocation.academic_year == academic_year,
                ResourceAllocation.status.in_(["DRAFT", "CONFIRMED"]),
                ResourceAllocation.day_of_week == day_to_iso[day_iso],
                ResourceAllocationResource.resource_id.in_(resource_ids),
                TimeSlot.code.in_(slot_codes),
            )
        ).all()
        occupied_resource_ids.update(faculty_conflicts)
        occupied_resource_ids.update(allocation_conflicts)

    return [
        ResourceCatalogItem(
            id=resource.id,
            code=resource.name,
            name=resource.name,
            resource_type=resource.resource_type,
        )
        for resource in resources
        if resource.id not in occupied_resource_ids
    ]


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
