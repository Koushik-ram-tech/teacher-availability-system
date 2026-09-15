"""
Resource Population Service

Populates the resources table from existing timetable data (room field).

This service:
1. Scans schedule_entries for unique room values
2. Creates Resource records with appropriate types
3. Creates ResourceAlias records for known variations
4. Links schedule_entries to resources via resource_id

Resource Type Classification:
- LAB* patterns → LAB
- FDC → LAB (Faculty Development Center)
- CA* patterns → CLASSROOM (Computer Applications classrooms)
- Unknown → OTHER

NO GUESSING: Only populate resource_id when the room string deterministically
identifies a single resource.
"""
from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.models import Resource, ResourceAlias, ScheduleEntry
from app.domain.resources import normalize_resource_name


ResourceType = Literal["LAB", "CLASSROOM", "SEMINAR_HALL", "AUDITORIUM", "OTHER"]


def classify_resource_type(room_name: str) -> ResourceType:
    """
    Classify a room name into a resource type.

    Classification rules:
    - Starts with "Lab" (case-insensitive) → LAB
    - "FDC" → LAB (Faculty Development Center is treated as lab)
    - Starts with "CA" followed by digit → CLASSROOM
    - Everything else → OTHER

    Examples:
    - "Lab1A" → LAB
    - "Lab 1A" → LAB
    - "FDC" → LAB
    - "CA1" → CLASSROOM
    - "CA2" → CLASSROOM
    - "CA3" → CLASSROOM
    - "Auditorium" → OTHER
    """
    name_lower = room_name.lower().strip()

    # Lab patterns
    if name_lower.startswith("lab"):
        return "LAB"

    # FDC is a lab
    if name_lower == "fdc":
        return "LAB"

    # CA followed by digit = classroom
    if re.match(r'^ca\d+$', name_lower):
        return "CLASSROOM"

    # Default
    return "OTHER"


def get_resource_inventory(db: Session) -> list[tuple[str, int]]:
    """
    Get all unique room names from schedule_entries with usage counts.

    Returns:
        List of (room_name, usage_count) tuples, sorted by room name
    """
    result = db.execute(
        select(ScheduleEntry.room, func.count(ScheduleEntry.id))
        .where(ScheduleEntry.room.isnot(None))
        .where(ScheduleEntry.room != "")
        .group_by(ScheduleEntry.room)
        .order_by(ScheduleEntry.room)
    ).all()

    return [(row[0], row[1]) for row in result]


def find_or_create_resource(
    db: Session,
    name: str,
    resource_type: ResourceType,
    department: str | None = None,
) -> Resource:
    """
    Find existing resource or create new one.

    Args:
        db: Database session
        name: Resource name (e.g., "Lab1A")
        resource_type: LAB, CLASSROOM, etc.
        department: Department name (None = shared resource)

    Returns:
        Resource instance (existing or newly created)
    """
    normalized = normalize_resource_name(name)

    # Check if resource already exists by normalized name
    scope_filter = (
        Resource.department == department
        if department
        else Resource.department.is_(None)
    )

    existing = db.execute(
        select(Resource)
        .where(
            Resource.normalized_name == normalized,
            scope_filter,
            Resource.is_active == True,
        )
    ).scalar_one_or_none()

    if existing:
        return existing

    # Create new resource with explicit UUID for SQLite compatibility
    from uuid import uuid4
    resource = Resource(
        id=uuid4(),  # Explicit ID for SQLite compatibility
        name=name,
        normalized_name=normalized,
        resource_type=resource_type,
        department=department,
        capacity=None,  # Unknown capacity
        is_active=True,
    )
    db.add(resource)
    db.flush()  # Get ID

    return resource


def create_alias_if_needed(
    db: Session,
    resource: Resource,
    alias: str,
) -> ResourceAlias | None:
    """
    Create a resource alias if the alias differs from the primary name.

    Args:
        db: Database session
        resource: The resource to create alias for
        alias: Alias string (e.g., "LAB 1A" as alias for "Lab1A")

    Returns:
        ResourceAlias instance or None if alias not needed
    """
    normalized_alias = normalize_resource_name(alias)

    # Don't create alias if it matches the resource's normalized name
    if normalized_alias == resource.normalized_name:
        return None

    # Check if alias already exists for this resource
    existing = db.execute(
        select(ResourceAlias)
        .where(
            ResourceAlias.resource_id == resource.id,
            ResourceAlias.normalized_alias == normalized_alias,
        )
    ).scalar_one_or_none()

    if existing:
        return existing

    # Check if alias is already used by a different resource
    conflicting = db.execute(
        select(ResourceAlias)
        .where(ResourceAlias.normalized_alias == normalized_alias)
        .where(ResourceAlias.resource_id != resource.id)
    ).scalar_one_or_none()

    if conflicting:
        # Ambiguous alias - don't create
        return None

    # Create alias with explicit UUID for SQLite compatibility
    from uuid import uuid4
    resource_alias = ResourceAlias(
        id=uuid4(),  # Explicit ID for SQLite compatibility
        resource_id=resource.id,
        alias=alias,
        normalized_alias=normalized_alias,
    )
    db.add(resource_alias)

    return resource_alias


def link_schedule_entries_to_resource(
    db: Session,
    resource: Resource,
    room_name: str,
) -> int:
    """
    Link all schedule_entries with matching room name to the resource.

    Args:
        db: Database session
        resource: The resource to link to
        room_name: Exact room name to match (case-sensitive for now)

    Returns:
        Number of schedule entries updated
    """
    entries = db.execute(
        select(ScheduleEntry)
        .where(ScheduleEntry.room == room_name)
        .where(ScheduleEntry.resource_id.is_(None))
    ).scalars().all()

    count = 0
    for entry in entries:
        entry.resource_id = resource.id
        count += 1

    return count


def populate_resources_from_timetable(
    db: Session,
    department: str | None = None,
    commit: bool = True,
) -> dict:
    """
    Populate resources table from existing schedule_entries.room data.

    This function:
    1. Scans all unique room values in schedule_entries
    2. Classifies each as LAB/CLASSROOM/OTHER
    3. Creates Resource records
    4. Creates common aliases (e.g., "Lab 1A" for "Lab1A")
    5. Links schedule_entries to resources via resource_id

    Args:
        db: Database session
        department: Department context (None = shared resources)
        commit: Whether to commit transaction

    Returns:
        Dict with population statistics
    """
    inventory = get_resource_inventory(db)

    stats = {
        "rooms_found": len(inventory),
        "resources_created": 0,
        "resources_reused": 0,
        "aliases_created": 0,
        "entries_linked": 0,
        "resources_by_type": {},
    }

    for room_name, usage_count in inventory:
        resource_type = classify_resource_type(room_name)

        # Find or create resource
        existing_count = db.execute(
            select(func.count(Resource.id))
            .where(
                Resource.normalized_name == normalize_resource_name(room_name),
                Resource.department.is_(None) if department is None else Resource.department == department,
            )
        ).scalar()

        resource = find_or_create_resource(
            db=db,
            name=room_name,
            resource_type=resource_type,
            department=department,
        )

        if existing_count == 0:
            stats["resources_created"] += 1
        else:
            stats["resources_reused"] += 1

        # Track by type
        stats["resources_by_type"][resource_type] = (
            stats["resources_by_type"].get(resource_type, 0) + 1
        )

        # Create common aliases
        # For "Lab1A", create alias "Lab 1A" (with space)
        if "lab" in room_name.lower() and " " not in room_name:
            # Insert space before digit
            spaced = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', room_name)
            if spaced != room_name:
                alias = create_alias_if_needed(db, resource, spaced)
                if alias:
                    stats["aliases_created"] += 1

        # For "Lab 1A", create alias "Lab1A" (without space)
        if "lab" in room_name.lower() and " " in room_name:
            no_space = room_name.replace(" ", "")
            if no_space != room_name:
                alias = create_alias_if_needed(db, resource, no_space)
                if alias:
                    stats["aliases_created"] += 1

        # Link schedule entries
        linked = link_schedule_entries_to_resource(db, resource, room_name)
        stats["entries_linked"] += linked

    if commit:
        db.commit()

    return stats
