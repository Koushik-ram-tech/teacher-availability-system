"""
Resource domain model and resolution.

This module provides the canonical resource representation and resolution logic
for the Teacher Availability System.

Key principles:
- KNOWN resource → resolve
- KNOWN unique alias → resolve
- UNKNOWN resource → unresolved ERROR
- AMBIGUOUS resource → ambiguous ERROR
- NEVER silently auto-create a resource
- Resource type is explicit, not inferred
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.domain.timetable import SourceLocation, ValidationIssue, ValidationSeverity
from app.models.models import Resource, ResourceAlias


# ---------------------------------------------------------------------------
# Resource Resolution Status
# ---------------------------------------------------------------------------


class ResourceResolutionStatus(str, Enum):
    """Status of resource resolution."""
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


# ---------------------------------------------------------------------------
# Resource Reference (Domain Model)
# ---------------------------------------------------------------------------


@dataclass
class ResourceReference:
    """
    Domain representation of a resource reference within a schedule activity.
    
    This tracks the resolution state of a resource from import/editing.
    
    Fields:
    - display_name: Original name from source (e.g. "Lab 1A" from XLSX)
    - normalized_name: Normalized form for matching
    - resolution_status: RESOLVED | UNRESOLVED | AMBIGUOUS
    - resolved_resource_id: UUID if resolved, None otherwise
    - department: Department context for scoped resolution
    - source_location: Where this resource reference came from
    - issues: Validation issues specific to this resource
    """
    
    display_name: str
    normalized_name: str
    resolution_status: ResourceResolutionStatus = ResourceResolutionStatus.UNRESOLVED
    resolved_resource_id: uuid.UUID | None = None
    department: str | None = None
    source_location: SourceLocation | None = None
    issues: list[ValidationIssue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Resource Normalization
# ---------------------------------------------------------------------------


def normalize_resource_name(name: str) -> str:
    """
    Normalize a resource name for matching.
    
    Normalization rules:
    1. Convert to lowercase
    2. Remove leading/trailing whitespace
    3. Collapse internal whitespace to single space
    4. Remove common punctuation that doesn't affect identity
    
    Examples:
    - "Lab 1A" → "lab 1a"
    - "LAB1A" → "lab1a"
    - "Lab  1A" → "lab 1a"
    - "Lab-1A" → "lab-1a" (hyphens preserved)
    - "Lab.1A" → "lab.1a" (periods preserved)
    
    NOTE: This is intentionally conservative. We do NOT:
    - Remove all punctuation (some may be significant)
    - Expand abbreviations
    - Apply fuzzy matching
    
    The goal is deterministic exact matching after minimal normalization.
    """
    if not name:
        return ""
    
    # Convert to lowercase
    normalized = name.lower()
    
    # Strip leading/trailing whitespace
    normalized = normalized.strip()
    
    # Collapse multiple spaces to single space
    normalized = re.sub(r'\s+', ' ', normalized)
    
    return normalized


# ---------------------------------------------------------------------------
# Resource Resolver
# ---------------------------------------------------------------------------


class ResourceResolver:
    """
    Resolves resource names to database Resource entities.
    
    Resolution order:
    1. Exact normalized resource name match in scope
    2. Unique explicit alias match in scope
    3. UNRESOLVED if no match
    4. AMBIGUOUS if multiple matches
    
    Scope rules:
    - Department-owned resources resolve within their department
    - Shared resources (department=NULL) are available to all
    - Search order: department-owned first, then shared
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def resolve(
        self,
        display_name: str,
        department: str | None = None,
        source_location: SourceLocation | None = None,
    ) -> ResourceReference:
        """
        Resolve a resource name to a Resource entity.
        
        Args:
            display_name: Original resource name from source
            department: Department context (None = any/shared)
            source_location: Where this reference came from
        
        Returns:
            ResourceReference with resolution status and resolved_resource_id
        """
        if not display_name or not display_name.strip():
            return ResourceReference(
                display_name=display_name or "",
                normalized_name="",
                resolution_status=ResourceResolutionStatus.UNRESOLVED,
                department=department,
                source_location=source_location,
            )
        
        normalized = normalize_resource_name(display_name)
        
        # Try to resolve
        matches = self._find_matches(normalized, department)
        
        if len(matches) == 0:
            # No match found
            return ResourceReference(
                display_name=display_name,
                normalized_name=normalized,
                resolution_status=ResourceResolutionStatus.UNRESOLVED,
                department=department,
                source_location=source_location,
            )
        
        elif len(matches) == 1:
            # Unique match - resolved
            resource = matches[0]
            return ResourceReference(
                display_name=display_name,
                normalized_name=normalized,
                resolution_status=ResourceResolutionStatus.RESOLVED,
                resolved_resource_id=resource.id,
                department=department,
                source_location=source_location,
            )
        
        else:
            # Multiple matches - ambiguous
            return ResourceReference(
                display_name=display_name,
                normalized_name=normalized,
                resolution_status=ResourceResolutionStatus.AMBIGUOUS,
                department=department,
                source_location=source_location,
            )
    
    def _find_matches(self, normalized_name: str, department: str | None) -> list[Resource]:
        """
        Find all active resources matching the normalized name in scope.
        
        Scope rules:
        - If department provided: search department-owned + shared
        - If no department: search all shared only
        
        Match methods:
        1. Direct resource.normalized_name match
        2. Resource alias match
        """
        matches: set[uuid.UUID] = set()
        
        # Build scope filter
        if department:
            # Department context: search department-owned OR shared
            scope_filter = or_(
                Resource.department == department,
                Resource.department.is_(None)
            )
        else:
            # No department: search shared only
            scope_filter = Resource.department.is_(None)
        
        # Find by direct resource name
        direct_matches = (
            self.db.query(Resource)
            .filter(
                Resource.normalized_name == normalized_name,
                Resource.is_active.is_(True),
                scope_filter
            )
            .all()
        )
        
        for resource in direct_matches:
            matches.add(resource.id)
        
        # Find by alias
        alias_matches = (
            self.db.query(Resource)
            .join(ResourceAlias)
            .filter(
                ResourceAlias.normalized_alias == normalized_name,
                Resource.is_active.is_(True),
                scope_filter
            )
            .all()
        )
        
        for resource in alias_matches:
            matches.add(resource.id)
        
        # Return deduplicated list
        return [
            self.db.query(Resource).filter(Resource.id == rid).one()
            for rid in matches
        ]
    
    def resolve_batch(
        self,
        names: list[tuple[str, str | None, SourceLocation | None]],
    ) -> list[ResourceReference]:
        """
        Resolve multiple resource names in a single batch.
        
        This is more efficient than calling resolve() repeatedly as it
        minimizes database round-trips.
        
        Args:
            names: List of (display_name, department, source_location) tuples
        
        Returns:
            List of ResourceReference objects in same order as input
        """
        # For now, use simple iteration
        # Future optimization: batch DB queries
        return [
            self.resolve(display_name, department, source_location)
            for display_name, department, source_location in names
        ]
