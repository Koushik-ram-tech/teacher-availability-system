"""Resolve rooms/resource codes and link them onto schedule entries.

Manual timetable writes and import persistence share this helper so a room
is never stored as a label without a ``ScheduleEntryResource`` row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.resources import normalize_resource_name
from app.models.models import (
    Resource,
    ResourceAllocation,
    ResourceAllocationResource,
    ScheduleEntry,
    ScheduleEntryResource,
)
from app.services.resource_populator import (
    classify_resource_type,
    create_alias_if_needed,
    find_or_create_resource,
)

ResourceCache = dict[str, Resource]


@dataclass
class ResourceLinkStats:
    created: int = 0
    reused: int = 0
    linked: int = 0


def resource_codes_for_entry(
    resource_codes: Sequence[str] | None,
    room: str | None,
) -> list[str]:
    """Prefer explicit codes; fall back to the room string as a single code."""
    if resource_codes:
        return [code.strip() for code in resource_codes if code and code.strip()]
    if room and room.strip():
        return [room.strip()]
    return []


def resolve_or_create_resource(
    db: Session,
    code: str,
    cache: ResourceCache,
) -> tuple[Resource, bool]:
    """Return ``(resource, created)`` for a shared (department-less) resource."""
    normalized = normalize_resource_name(code)
    if normalized in cache:
        return cache[normalized], False

    existing_count = db.execute(
        select(func.count(Resource.id))
        .where(Resource.normalized_name == normalized)
        .where(Resource.department.is_(None))
    ).scalar()

    resource = find_or_create_resource(
        db=db,
        name=code,
        resource_type=classify_resource_type(code),
        department=None,
    )
    cache[normalized] = resource
    created = existing_count == 0

    if "lab" in code.lower():
        if " " not in code:
            spaced = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", code)
            if spaced != code:
                create_alias_if_needed(db, resource, spaced)
        else:
            no_space = code.replace(" ", "")
            if no_space != code:
                create_alias_if_needed(db, resource, no_space)

    return resource, created


def link_schedule_entry_resources(
    db: Session,
    entry: ScheduleEntry,
    resource_codes: Sequence[str] | None,
    room: str | None,
    cache: ResourceCache,
) -> ResourceLinkStats:
    """Create ``ScheduleEntryResource`` rows for an entry's rooms/codes."""
    stats = ResourceLinkStats()
    for code in resource_codes_for_entry(resource_codes, room):
        resource, created = resolve_or_create_resource(db, code, cache)
        if created:
            stats.created += 1
        else:
            stats.reused += 1
        db.add(
            ScheduleEntryResource(
                schedule_entry_id=entry.id,
                resource_id=resource.id,
            )
        )
        if entry.resource_id is None:
            entry.resource_id = resource.id
        stats.linked += 1
    return stats


def link_allocation_resources(
    db: Session,
    allocation: ResourceAllocation,
    resource_codes: Sequence[str] | None,
    room: str | None,
    cache: ResourceCache,
) -> ResourceLinkStats:
    """Create ``ResourceAllocationResource`` rows for an external allocation."""
    stats = ResourceLinkStats()
    for code in resource_codes_for_entry(resource_codes, room):
        resource, created = resolve_or_create_resource(db, code, cache)
        if created:
            stats.created += 1
        else:
            stats.reused += 1
        db.add(
            ResourceAllocationResource(
                allocation_id=allocation.id,
                resource_id=resource.id,
            )
        )
        stats.linked += 1
    return stats
