"""Availability service for teacher and resource FREE/BUSY status.

PRODUCT REQUIREMENT:
Determine which slots a teacher/resource is BUSY or FREE.
Does NOT require teacher→subject or resource→subject relationships.

Architecture:
    Confirmed Timetable Data → Availability Service → FREE/BUSY Status

FREE is derived as: working_slots - occupied_slots
"""

from dataclasses import dataclass
from typing import Optional, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.models import (
    Resource, ResourceAlias, ScheduleEntry, ScheduleEntryResource, ScheduleEntrySlot,
    Teacher, TimeSlot, Timetable, ResourceAllocation, ResourceAllocationSlot, ResourceAllocationResource
)
from app.core.schedule_config import ISO_TO_DAY_NAME


# Working slot definitions (institution's fixed grid)
WORKING_SLOTS = {
    "S1": {"start": "08:00", "end": "08:55"},
    "S2": {"start": "08:55", "end": "09:50"},
    "S3": {"start": "09:50", "end": "10:45"},
    # 10:45-11:15 = coffee break (not a working slot)
    "S4": {"start": "11:15", "end": "12:10"},
    "S5": {"start": "12:10", "end": "13:05"},
    # 13:05-14:00 = lunch break (not a working slot)
    "S6": {"start": "14:00", "end": "14:55"},
    "S7": {"start": "14:55", "end": "15:50"},
    "S8": {"start": "15:50", "end": "16:45"},
    "S9": {"start": "16:45", "end": "17:40"},
}

WORKING_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
# Sunday is non-working


@dataclass
class SlotAvailability:
    """Availability status for a single slot."""
    slot_code: str
    status: str  # "FREE" | "OCCUPIED" | "UNKNOWN"

    # Optional supporting metadata (if available)
    subject_or_activity: Optional[str] = None
    section: Optional[str] = None
    room: Optional[str] = None


@dataclass
class DayAvailability:
    """Availability for all slots in a day."""
    day: str  # ISO day name: "monday", "tuesday", etc.
    slots: dict[str, SlotAvailability]  # slot_code → SlotAvailability


@dataclass
class TeacherAvailability:
    """Teacher weekly or daily availability."""
    teacher_id: UUID
    teacher_name: str
    teacher_acronym: str
    academic_year: str
    days: dict[str, DayAvailability]  # day → DayAvailability


@dataclass
class ResourceAvailability:
    """Resource weekly or daily availability."""
    resource_id: UUID
    resource_code: str
    resource_name: str
    academic_year: str
    days: dict[str, DayAvailability]  # day → DayAvailability


class AvailabilityService:
    """Service for deriving teacher and resource availability from timetable data.

    PRINCIPLE:
    - OCCUPIED: Confirmed occupancy exists in timetable
    - FREE: No confirmed occupancy (default for working slots)
    - UNKNOWN: Allocation genuinely ambiguous (not implemented in Phase 2)

    Does NOT require subject parsing or teacher→subject relationships.
    """

    @classmethod
    def get_teacher_availability(
        cls,
        db: Session,
        teacher_id: UUID,
        academic_year: str,
        day: Optional[str] = None
    ) -> Optional[TeacherAvailability]:
        """Get teacher availability for week or specific day.

        Args:
            db: Database session
            teacher_id: Teacher UUID
            academic_year: Academic year (e.g., "2026-Odd")
            day: Optional ISO day name (e.g., "monday")

        Returns:
            TeacherAvailability or None if teacher not found
        """
        # Get teacher
        teacher = db.scalar(
            select(Teacher).where(Teacher.id == teacher_id)
        )
        if not teacher:
            return None

        # Get confirmed timetable for this teacher and academic year
        timetable = db.scalar(
            select(Timetable)
            .options(
                selectinload(Timetable.entries)
                .selectinload(ScheduleEntry.slot_links)
                .selectinload(ScheduleEntrySlot.time_slot)
            )
            .where(
                Timetable.teacher_id == teacher_id,
                Timetable.academic_year == academic_year,
                Timetable.status == "CONFIRMED"
            )
        )

        # If no confirmed timetable, all slots are FREE
        if not timetable:
            return cls._build_teacher_availability_all_free(
                teacher=teacher,
                academic_year=academic_year,
                day=day
            )

        # Build occupancy map from timetable entries
        occupancy_map = cls._build_occupancy_map(timetable.entries)

        # Determine which days to include
        days_to_include = [day] if day else WORKING_DAYS

        # Build availability for each day
        days_availability = {}
        for day_name in days_to_include:
            day_occupancy = occupancy_map.get(day_name, {})
            days_availability[day_name] = cls._build_day_availability(
                day=day_name,
                occupancy=day_occupancy
            )

        return TeacherAvailability(
            teacher_id=teacher.id,
            teacher_name=teacher.name,
            teacher_acronym=teacher.acronym,
            academic_year=academic_year,
            days=days_availability
        )

    @classmethod
    def get_resource_availability(
        cls,
        db: Session,
        resource_id: UUID,
        academic_year: str,
        day: Optional[str] = None
    ) -> Optional[ResourceAvailability]:
        """Get resource availability for week or specific day.

        Args:
            db: Database session
            resource_id: Resource UUID
            academic_year: Academic year
            day: Optional ISO day name

        Returns:
            ResourceAvailability or None if resource not found
        """
        # Get resource
        resource = db.scalar(
            select(Resource).where(Resource.id == resource_id)
        )
        if not resource:
            return None

        # Get all schedule entries that use this resource via the join table.
        # The join table is authoritative for multi-resource cases.
        entries = db.scalars(
            select(ScheduleEntry)
            .join(
                ScheduleEntryResource,
                ScheduleEntryResource.schedule_entry_id == ScheduleEntry.id,
            )
            .where(ScheduleEntryResource.resource_id == resource_id)
            .options(
                selectinload(ScheduleEntry.slot_links)
                .selectinload(ScheduleEntrySlot.time_slot),
                selectinload(ScheduleEntry.timetable)
            )
        ).all()

        # Filter entries by academic year and confirmed status
        confirmed_entries = [
            entry for entry in entries
            if entry.timetable.academic_year == academic_year
            and entry.timetable.status == "CONFIRMED"
        ]

        # Get all external resource allocations that use this resource
        allocations = db.scalars(
            select(ResourceAllocation)
            .join(
                ResourceAllocationResource,
                ResourceAllocationResource.allocation_id == ResourceAllocation.id,
            )
            .where(
                ResourceAllocationResource.resource_id == resource.id,
                ResourceAllocation.academic_year == academic_year,
                ResourceAllocation.status == "CONFIRMED"
            )
            .options(
                selectinload(ResourceAllocation.slot_links)
                .selectinload(ResourceAllocationSlot.time_slot)
            )
        ).all()

        # Combine both faculty-backed and external-only entries
        all_confirmed: list[Any] = confirmed_entries + list(allocations)

        # Build occupancy map from entries
        occupancy_map = cls._build_occupancy_map(all_confirmed)

        # Determine which days to include
        days_to_include = [day] if day else WORKING_DAYS

        # Build availability for each day
        days_availability = {}
        for day_name in days_to_include:
            day_occupancy = occupancy_map.get(day_name, {})
            days_availability[day_name] = cls._build_day_availability(
                day=day_name,
                occupancy=day_occupancy
            )

        return ResourceAvailability(
            resource_id=resource.id,
            resource_code=resource.name,  # Primary name
            resource_name=resource.name,
            academic_year=academic_year,
            days=days_availability
        )

    @classmethod
    def find_resource_by_code(
        cls,
        db: Session,
        code: str
    ) -> Optional[Resource]:
        """Find resource by code or alias with normalization.

        Respects the existing resource normalization/alias system.
        "LAB1A" and "LAB 1A" resolve to the same resource.

        Args:
            db: Database session
            code: Resource code (may be alias)

        Returns:
            Resource or None if not found
        """
        # Normalize code
        normalized_code = cls._normalize_resource_code(code)

        # Try exact match on normalized_name
        resource = db.scalar(
            select(Resource).where(Resource.normalized_name == normalized_code)
        )
        if resource:
            return resource

        # Try alias match
        alias = db.scalar(
            select(ResourceAlias)
            .options(selectinload(ResourceAlias.resource))
            .where(ResourceAlias.normalized_alias == normalized_code)
        )
        if alias:
            return alias.resource

        return None

    @classmethod
    def _normalize_resource_code(cls, code: str) -> str:
        """Normalize resource code for comparison.

        Uses the same normalization as domain.resources.normalize_resource_name.

        Args:
            code: Raw resource code

        Returns:
            Normalized code (lowercase, collapsed spaces)
        """
        from app.domain.resources import normalize_resource_name
        return normalize_resource_name(code)

    @classmethod
    def _build_teacher_availability_all_free(
        cls,
        teacher: Teacher,
        academic_year: str,
        day: Optional[str]
    ) -> TeacherAvailability:
        """Build availability with all slots FREE (no timetable exists).

        Args:
            teacher: Teacher entity
            academic_year: Academic year
            day: Optional specific day

        Returns:
            TeacherAvailability with all FREE slots
        """
        days_to_include = [day] if day else WORKING_DAYS

        days_availability = {}
        for day_name in days_to_include:
            days_availability[day_name] = cls._build_day_availability(
                day=day_name,
                occupancy={}  # No occupancy = all FREE
            )

        return TeacherAvailability(
            teacher_id=teacher.id,
            teacher_name=teacher.name,
            teacher_acronym=teacher.acronym,
            academic_year=academic_year,
            days=days_availability
        )

    @classmethod
    def _build_occupancy_map(
        cls,
        entries: list[Any]
    ) -> dict[str, dict[str, Any]]:
        """Build occupancy map from schedule entries or resource allocations.

        Args:
            entries: List of ScheduleEntry or ResourceAllocation objects with loaded slot_links

        Returns:
            Nested dict: day → slot_code → entry
        """
        occupancy_map = {}

        for entry in entries:
            # Convert day_of_week (1-7) to ISO day name
            day_name = ISO_TO_DAY_NAME.get(entry.day_of_week)
            if not day_name:
                continue  # Skip invalid days

            if day_name not in occupancy_map:
                occupancy_map[day_name] = {}

            # Get all slot codes for this entry
            for slot_link in entry.slot_links:
                slot_code = slot_link.time_slot.code
                # Store entry for this day/slot combination
                # If multiple entries exist for same slot (shouldn't happen), last wins
                occupancy_map[day_name][slot_code] = entry

        return occupancy_map

    @classmethod
    def _build_day_availability(
        cls,
        day: str,
        occupancy: dict[str, Any]
    ) -> DayAvailability:
        """Build availability for a single day.

        Args:
            day: ISO day name
            occupancy: Dict of slot_code → Entry for occupied slots

        Returns:
            DayAvailability with FREE/OCCUPIED status for all working slots
        """
        slots = {}

        for slot_code in WORKING_SLOTS.keys():
            if slot_code in occupancy:
                # Slot is OCCUPIED
                entry = occupancy[slot_code]
                slots[slot_code] = SlotAvailability(
                    slot_code=slot_code,
                    status="OCCUPIED",
                    subject_or_activity=entry.subject_or_activity,
                    section=entry.section,
                    room=getattr(entry, 'room', None)
                )
            else:
                # Slot is FREE (no occupancy)
                slots[slot_code] = SlotAvailability(
                    slot_code=slot_code,
                    status="FREE"
                )

        return DayAvailability(
            day=day,
            slots=slots
        )

def check_resource_conflicts(
    db: Session,
    academic_year: str,
    resource_ids: list[UUID],
    day_of_week: int,
    time_slot_ids: list[UUID],
    ignore_timetable_id: Optional[UUID] = None
) -> tuple[bool, Optional[str]]:
    """Check if any of the given resources are already occupied at the given slots.

    Args:
        db: Database session
        academic_year: The academic year
        resource_ids: List of resource UUIDs to check
        day_of_week: ISO day of week (1=Monday...7=Sunday)
        time_slot_ids: List of time slot UUIDs to check
        ignore_timetable_id: If replacing a timetable, ignore its current confirmed entries

    Returns:
        (has_conflict, error_message)
    """
    if not resource_ids or not time_slot_ids:
        return False, None

    # Check faculty-backed confirmed entries
    query_se = (
        select(ScheduleEntryResource.resource_id, TimeSlot.code)
        .join(ScheduleEntry, ScheduleEntry.id == ScheduleEntryResource.schedule_entry_id)
        .join(Timetable, Timetable.id == ScheduleEntry.timetable_id)
        .join(ScheduleEntrySlot, ScheduleEntrySlot.schedule_entry_id == ScheduleEntry.id)
        .join(TimeSlot, TimeSlot.id == ScheduleEntrySlot.time_slot_id)
        .where(
            Timetable.academic_year == academic_year,
            Timetable.status == "CONFIRMED",
            ScheduleEntry.day_of_week == day_of_week,
            ScheduleEntryResource.resource_id.in_(resource_ids),
            ScheduleEntrySlot.time_slot_id.in_(time_slot_ids)
        )
    )
    if ignore_timetable_id:
        query_se = query_se.where(Timetable.id != ignore_timetable_id)

    conflict_se = db.execute(query_se).first()
    if conflict_se:
        r_id, s_code = conflict_se
        return True, f"Resource is already occupied by a confirmed teacher timetable at slot {s_code}."

    # Check external resource allocations
    query_ra = (
        select(ResourceAllocationResource.resource_id, TimeSlot.code)
        .join(ResourceAllocation, ResourceAllocation.id == ResourceAllocationResource.allocation_id)
        .join(ResourceAllocationSlot, ResourceAllocationSlot.allocation_id == ResourceAllocation.id)
        .join(TimeSlot, TimeSlot.id == ResourceAllocationSlot.time_slot_id)
        .where(
            ResourceAllocation.academic_year == academic_year,
            ResourceAllocation.status == "CONFIRMED",
            ResourceAllocation.day_of_week == day_of_week,
            ResourceAllocationResource.resource_id.in_(resource_ids),
            ResourceAllocationSlot.time_slot_id.in_(time_slot_ids)
        )
    )
    conflict_ra = db.execute(query_ra).first()
    if conflict_ra:
        r_id, s_code = conflict_ra
        return True, f"Resource is already occupied by an external participant at slot {s_code}."

    return False, None
