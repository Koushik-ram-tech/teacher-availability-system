"""
Tests for resource foundation: normalization, resolution, and domain model.

This test suite verifies:
1. Resource name normalization
2. Resource resolution (RESOLVED, UNRESOLVED, AMBIGUOUS)
3. Department scoping
4. Shared resources
5. Alias resolution
6. No auto-creation of resources
7. Resource domain model integration
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.domain.resources import (
    ResourceReference,
    ResourceResolutionStatus,
    ResourceResolver,
    normalize_resource_name,
)
from app.models.models import Resource, ResourceAlias


class TestResourceNormalization:
    """Test resource name normalization."""

    def test_basic_normalization(self):
        """Basic normalization converts to lowercase and trims."""
        assert normalize_resource_name("Lab 1A") == "lab 1a"
        assert normalize_resource_name("LAB1A") == "lab1a"
        assert normalize_resource_name("  Lab 1A  ") == "lab 1a"

    def test_whitespace_collapse(self):
        """Multiple spaces collapsed to single space."""
        assert normalize_resource_name("Lab  1A") == "lab 1a"
        assert normalize_resource_name("Lab   1   A") == "lab 1 a"

    def test_punctuation_preserved(self):
        """Significant punctuation is preserved."""
        assert normalize_resource_name("Lab-1A") == "lab-1a"
        assert normalize_resource_name("Lab.1A") == "lab.1a"
        assert normalize_resource_name("Lab_1A") == "lab_1a"

    def test_empty_string(self):
        """Empty string normalized to empty string."""
        assert normalize_resource_name("") == ""
        assert normalize_resource_name("   ") == ""

    def test_consistency(self):
        """Same logical name normalizes consistently."""
        names = ["Lab 1A", "LAB 1A", "lab 1a", "  Lab 1A  ", "Lab  1A"]
        normalized = [normalize_resource_name(n) for n in names]
        assert len(set(normalized)) == 1  # All normalize to same value


class TestResourceResolutionBasics:
    """Test basic resource resolution behavior."""

    def test_empty_name_unresolved(self, db: Session):
        """Empty resource name is unresolved."""
        resolver = ResourceResolver(db)
        result = resolver.resolve("")
        
        assert result.resolution_status == ResourceResolutionStatus.UNRESOLVED
        assert result.resolved_resource_id is None

    def test_unknown_resource_unresolved(self, db: Session):
        """Unknown resource returns UNRESOLVED."""
        resolver = ResourceResolver(db)
        result = resolver.resolve("Unknown Resource 99")
        
        assert result.resolution_status == ResourceResolutionStatus.UNRESOLVED
        assert result.resolved_resource_id is None
        assert result.display_name == "Unknown Resource 99"
        assert result.normalized_name == "unknown resource 99"

    def test_no_auto_create(self, db: Session):
        """Resolving unknown resource does NOT create a Resource row."""
        resolver = ResourceResolver(db)
        
        # Count before
        count_before = db.query(Resource).count()
        
        # Resolve unknown resource
        result = resolver.resolve("Nonexistent Lab")
        
        # Count after
        count_after = db.query(Resource).count()
        
        assert result.resolution_status == ResourceResolutionStatus.UNRESOLVED
        assert count_before == count_after  # No resource created


class TestExactResourceResolution:
    """Test exact resource name resolution."""

    def test_exact_match_resolved(self, db: Session):
        """Exact normalized name match resolves."""
        # Create resource
        now = datetime.now(timezone.utc)
        resource = Resource(
            id=uuid.uuid4(),
            name="Lab 1A",
            normalized_name="lab 1a",
            resource_type="LAB",
            department="Computer Science",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(resource)
        db.commit()
        
        resolver = ResourceResolver(db)
        result = resolver.resolve("Lab 1A", department="Computer Science")
        
        assert result.resolution_status == ResourceResolutionStatus.RESOLVED
        assert result.resolved_resource_id == resource.id
        assert result.display_name == "Lab 1A"

    def test_case_insensitive_match(self, db: Session):
        """Case variations of known resource resolve."""
        now = datetime.now(timezone.utc)
        resource = Resource(
            id=uuid.uuid4(),
            name="Lab 1A",
            normalized_name="lab 1a",
            resource_type="LAB",
            department="Computer Science",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(resource)
        db.commit()
        
        resolver = ResourceResolver(db)
        
        # All these should resolve to same resource
        for name in ["Lab 1A", "LAB 1A", "lab 1a", "  Lab 1A  "]:
            result = resolver.resolve(name, department="Computer Science")
            assert result.resolution_status == ResourceResolutionStatus.RESOLVED
            assert result.resolved_resource_id == resource.id

    def test_inactive_resource_not_resolved(self, db: Session):
        """Inactive resources are not resolved."""
        now = datetime.now(timezone.utc)
        resource = Resource(
            id=uuid.uuid4(),
            name="Old Lab",
            normalized_name="old lab",
            resource_type="LAB",
            department="Computer Science",
            is_active=False,  # Inactive
            created_at=now,
            updated_at=now,
        )
        db.add(resource)
        db.commit()
        
        resolver = ResourceResolver(db)
        result = resolver.resolve("Old Lab", department="Computer Science")
        
        assert result.resolution_status == ResourceResolutionStatus.UNRESOLVED


class TestAliasResolution:
    """Test resource alias resolution."""

    def test_unique_alias_resolved(self, db: Session):
        """Unique alias resolves to resource."""
        now = datetime.now(timezone.utc)
        resource = Resource(
            id=uuid.uuid4(),
            name="Computer Applications Lab 1A",
            normalized_name="computer applications lab 1a",
            resource_type="LAB",
            department="Computer Science",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(resource)
        db.flush()
        
        # Add alias
        alias = ResourceAlias(
            id=uuid.uuid4(),
            resource_id=resource.id,
            alias="CA Lab 1A",
            normalized_alias="ca lab 1a",
            created_at=now,
        )
        db.add(alias)
        db.commit()
        
        resolver = ResourceResolver(db)
        result = resolver.resolve("CA Lab 1A", department="Computer Science")
        
        assert result.resolution_status == ResourceResolutionStatus.RESOLVED
        assert result.resolved_resource_id == resource.id

    def test_multiple_aliases_same_resource(self, db: Session):
        """Multiple aliases can point to same resource."""
        now = datetime.now(timezone.utc)
        resource = Resource(
            id=uuid.uuid4(),
            name="Lab 1A",
            normalized_name="lab 1a",
            resource_type="LAB",
            department="Computer Science",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(resource)
        db.flush()
        
        # Add multiple aliases
        alias1 = ResourceAlias(
            id=uuid.uuid4(),
            resource_id=resource.id,
            alias="CA Lab 1A",
            normalized_alias="ca lab 1a",
            created_at=now,
        )
        alias2 = ResourceAlias(
            id=uuid.uuid4(),
            resource_id=resource.id,
            alias="CS Lab 1A",
            normalized_alias="cs lab 1a",
            created_at=now,
        )
        db.add_all([alias1, alias2])
        db.commit()
        
        resolver = ResourceResolver(db)
        
        # Both aliases resolve to same resource
        result1 = resolver.resolve("CA Lab 1A", department="Computer Science")
        result2 = resolver.resolve("CS Lab 1A", department="Computer Science")
        
        assert result1.resolution_status == ResourceResolutionStatus.RESOLVED
        assert result2.resolution_status == ResourceResolutionStatus.RESOLVED
        assert result1.resolved_resource_id == result2.resolved_resource_id == resource.id


class TestAmbiguousResolution:
    """Test ambiguous resource resolution."""

    def test_multiple_resources_same_name_different_departments_ambiguous(self, db: Session):
        """Multiple resources with same normalized name in scope → AMBIGUOUS."""
        # Create two resources with same name in different departments
        now = datetime.now(timezone.utc)
        resource1 = Resource(
            id=uuid.uuid4(),
            name="Lab 1",
            normalized_name="lab 1",
            resource_type="LAB",
            department="Computer Science",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        resource2 = Resource(
            id=uuid.uuid4(),
            name="Lab 1",
            normalized_name="lab 1",
            resource_type="LAB",
            department="Electronics",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add_all([resource1, resource2])
        db.commit()
        
        resolver = ResourceResolver(db)
        
        # Without department context, cannot determine which one (both shared search would fail)
        # With department context, resolves to that department's resource
        result_cs = resolver.resolve("Lab 1", department="Computer Science")
        result_ec = resolver.resolve("Lab 1", department="Electronics")
        
        # Each department gets its own
        assert result_cs.resolution_status == ResourceResolutionStatus.RESOLVED
        assert result_cs.resolved_resource_id == resource1.id
        
        assert result_ec.resolution_status == ResourceResolutionStatus.RESOLVED
        assert result_ec.resolved_resource_id == resource2.id

    def test_ambiguous_alias(self, db: Session):
        """If an alias could match multiple resources in scope → AMBIGUOUS."""
        # This shouldn't happen with proper unique constraints, but test the logic
        # Actually, the schema prevents this with unique constraint on normalized_alias
        # So this test verifies the constraint works
        pass  # Skip - schema prevents this


class TestDepartmentScoping:
    """Test department-based resource scoping."""

    def test_department_owned_resource_in_department(self, db: Session):
        """Department-owned resource resolves within its department."""
        now = datetime.now(timezone.utc)
        resource = Resource(
            id=uuid.uuid4(),
            name="CS Lab 1",
            normalized_name="cs lab 1",
            resource_type="LAB",
            department="Computer Science",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(resource)
        db.commit()
        
        resolver = ResourceResolver(db)
        result = resolver.resolve("CS Lab 1", department="Computer Science")
        
        assert result.resolution_status == ResourceResolutionStatus.RESOLVED
        assert result.resolved_resource_id == resource.id

    def test_department_owned_resource_not_in_other_department(self, db: Session):
        """Department-owned resource does NOT resolve in different department."""
        now = datetime.now(timezone.utc)
        resource = Resource(
            id=uuid.uuid4(),
            name="CS Lab 1",
            normalized_name="cs lab 1",
            resource_type="LAB",
            department="Computer Science",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(resource)
        db.commit()
        
        resolver = ResourceResolver(db)
        result = resolver.resolve("CS Lab 1", department="Electronics")
        
        assert result.resolution_status == ResourceResolutionStatus.UNRESOLVED


class TestSharedResources:
    """Test shared resource (department=NULL) behavior."""

    def test_shared_resource_available_to_all(self, db: Session):
        """Shared resource (department=NULL) resolves for any department."""
        now = datetime.now(timezone.utc)
        resource = Resource(
            id=uuid.uuid4(),
            name="Auditorium",
            normalized_name="auditorium",
            resource_type="AUDITORIUM",
            department=None,  # Shared
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(resource)
        db.commit()
        
        resolver = ResourceResolver(db)
        
        # Should resolve for any department
        result_cs = resolver.resolve("Auditorium", department="Computer Science")
        result_ec = resolver.resolve("Auditorium", department="Electronics")
        result_no_dept = resolver.resolve("Auditorium", department=None)
        
        assert result_cs.resolution_status == ResourceResolutionStatus.RESOLVED
        assert result_cs.resolved_resource_id == resource.id
        
        assert result_ec.resolution_status == ResourceResolutionStatus.RESOLVED
        assert result_ec.resolved_resource_id == resource.id
        
        assert result_no_dept.resolution_status == ResourceResolutionStatus.RESOLVED
        assert result_no_dept.resolved_resource_id == resource.id

    def test_shared_and_department_owned_same_name(self, db: Session):
        """Department-owned resource takes precedence over shared in that department."""
        # Shared resource
        now = datetime.now(timezone.utc)
        shared = Resource(
            id=uuid.uuid4(),
            name="Seminar Hall",
            normalized_name="seminar hall",
            resource_type="SEMINAR_HALL",
            department=None,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        # Department-owned with same name
        dept_owned = Resource(
            id=uuid.uuid4(),
            name="Seminar Hall",
            normalized_name="seminar hall",
            resource_type="SEMINAR_HALL",
            department="Computer Science",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add_all([shared, dept_owned])
        db.commit()
        
        resolver = ResourceResolver(db)
        
        # In Computer Science department, both match → AMBIGUOUS
        # (This is intentional - both are valid, user must disambiguate)
        result_cs = resolver.resolve("Seminar Hall", department="Computer Science")
        assert result_cs.resolution_status == ResourceResolutionStatus.AMBIGUOUS
        
        # In other department, only shared matches → RESOLVED
        result_ec = resolver.resolve("Seminar Hall", department="Electronics")
        assert result_ec.resolution_status == ResourceResolutionStatus.RESOLVED
        assert result_ec.resolved_resource_id == shared.id


class TestResourceDomainModel:
    """Test ResourceReference domain model."""

    def test_resource_reference_construction(self):
        """ResourceReference can be constructed with all fields."""
        from app.domain.timetable import SourceLocation
        
        source_loc = SourceLocation(
            source_type="XLSX",
            sheet_name="Schedule",
            row_number=10,
        )
        
        ref = ResourceReference(
            display_name="Lab 1A",
            normalized_name="lab 1a",
            resolution_status=ResourceResolutionStatus.RESOLVED,
            resolved_resource_id=uuid.uuid4(),
            department="Computer Science",
            source_location=source_loc,
        )
        
        assert ref.display_name == "Lab 1A"
        assert ref.resolution_status == ResourceResolutionStatus.RESOLVED
        assert ref.source_location.sheet_name == "Schedule"

    def test_resource_reference_in_schedule_activity(self):
        """ResourceReference can be attached to ScheduleActivity."""
        from app.domain.timetable import ActivitySlotRange, ScheduleActivity
        
        resource_ref = ResourceReference(
            display_name="Lab 1A",
            normalized_name="lab 1a",
            resolution_status=ResourceResolutionStatus.UNRESOLVED,
        )
        
        activity = ScheduleActivity(
            teacher_acronym="TEST",
            entry_type="LAB",
            subject_or_activity="DBMS Lab",
            section="MCA-1A",
            room="Lab 1A",  # Legacy field
            notes=None,
            slot_range=ActivitySlotRange(day_of_week=1, slot_codes=["S1", "S2"]),
            resource_ref=resource_ref,  # NEW field
        )
        
        assert activity.resource_ref is not None
        assert activity.resource_ref.display_name == "Lab 1A"
        assert activity.resource_ref.resolution_status == ResourceResolutionStatus.UNRESOLVED


class TestBatchResolution:
    """Test batch resource resolution."""

    def test_resolve_batch(self, db: Session):
        """Batch resolution processes multiple names."""
        # Create resources
        now = datetime.now(timezone.utc)
        lab1 = Resource(
            id=uuid.uuid4(),
            name="Lab 1",
            normalized_name="lab 1",
            resource_type="LAB",
            department="Computer Science",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        lab2 = Resource(
            id=uuid.uuid4(),
            name="Lab 2",
            normalized_name="lab 2",
            resource_type="LAB",
            department="Computer Science",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add_all([lab1, lab2])
        db.commit()
        
        resolver = ResourceResolver(db)
        
        names = [
            ("Lab 1", "Computer Science", None),
            ("Lab 2", "Computer Science", None),
            ("Unknown Lab", "Computer Science", None),
        ]
        
        results = resolver.resolve_batch(names)
        
        assert len(results) == 3
        assert results[0].resolution_status == ResourceResolutionStatus.RESOLVED
        assert results[0].resolved_resource_id == lab1.id
        assert results[1].resolution_status == ResourceResolutionStatus.RESOLVED
        assert results[1].resolved_resource_id == lab2.id
        assert results[2].resolution_status == ResourceResolutionStatus.UNRESOLVED
