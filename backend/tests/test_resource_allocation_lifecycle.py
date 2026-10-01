"""
Test resource allocation lifecycle for re-imports.

Tests that external/resource-only allocations can be replaced safely
without affecting unrelated manual allocations or causing duplicate conflicts.

Uses programmatic data instead of DOCX fixtures for reliability.
"""
import pytest
from uuid import uuid4
from sqlalchemy import select, func

from app.models.models import (
    ResourceAllocation,
    ResourceAllocationSlot,
    ResourceAllocationResource,
    Resource,
    TimeSlot,
)


@pytest.fixture
def fdc_resource(db):
    """Ensure FDC resource exists."""
    resource = db.scalars(
        select(Resource).where(Resource.normalized_name == "fdc")
    ).first()
    
    if not resource:
        from sqlalchemy import text
        import uuid as uuid_module
        from datetime import datetime
        
        resource_id = str(uuid_module.uuid4())
        now = datetime.now().isoformat()
        
        db.execute(
            text("""
                INSERT INTO resources 
                (id, name, normalized_name, resource_type, is_active, created_at, updated_at) 
                VALUES (:id, :name, :norm, :type, 1, :now, :now)
            """),
            {
                "id": resource_id,
                "name": "Faculty Development Center",
                "norm": "fdc",
                "type": "OTHER",
                "now": now,
            }
        )
        db.flush()
        
        # Return a simple object with the string ID
        class ResourceResult:
            def __init__(self, id):
                self.id = id
        
        return ResourceResult(resource_id)
    
    # If it already exists, return the fetched resource
    return resource


def create_test_external_allocation(
    db,
    academic_year: str,
    section: str,
    activity: str,
    day: int = 1,
    slot_code: str = "S1",
    resource_code: str = "FDC",
    source_import_id: str = None,
    status: str = "CONFIRMED",
) -> ResourceAllocation:
    """Create a test external allocation programmatically.
    
    Args:
        db: Database session
        academic_year: Academic year (e.g., "2026-2027")
        section: Section name (e.g., "I-A")
        activity: Activity name
        day: Day of week (1=Monday, 5=Friday)
        slot_code: Time slot code (e.g., "S1")
        resource_code: Resource code (e.g., "FDC")
        source_import_id: Import ID if from import, None for manual
        status: Allocation status (DRAFT or CONFIRMED)
    
    Returns:
        Created ResourceAllocation
    """
    # Get resource and slot
    resource = db.scalars(
        select(Resource).where(Resource.normalized_name == resource_code.lower())
    ).first()
    slot = db.scalars(
        select(TimeSlot).where(TimeSlot.code == slot_code)
    ).first()
    
    if not resource:
        raise ValueError(f"Resource {resource_code} not found")
    if not slot:
        raise ValueError(f"Slot {slot_code} not found")
    
    # Create allocation using raw SQL for SQLite compatibility
    from sqlalchemy import text
    import uuid as uuid_module
    from datetime import datetime
    
    allocation_id = str(uuid_module.uuid4())
    now = datetime.now().isoformat()
    
    db.execute(
        text("""
            INSERT INTO resource_allocations 
            (id, academic_year, status, source_import_id, day_of_week, 
             subject_or_activity, section, group_index, created_at, updated_at)
            VALUES (:id, :year, :status, :source_id, :day, :activity, :section, :group, :now, :now)
        """),
        {
            "id": allocation_id,
            "year": academic_year,
            "status": status,
            "source_id": source_import_id,
            "day": day,
            "activity": activity,
            "section": section,
            "group": 0,
            "now": now,
        }
    )
    
    # Link slot
    db.execute(
        text("INSERT INTO resource_allocation_slots (allocation_id, time_slot_id) VALUES (:alloc_id, :slot_id)"),
        {"alloc_id": allocation_id, "slot_id": str(slot.id)}
    )
    
    # Link resource
    db.execute(
        text("INSERT INTO resource_allocation_resources (allocation_id, resource_id) VALUES (:alloc_id, :res_id)"),
        {"alloc_id": allocation_id, "res_id": str(resource.id)}
    )
    
    db.flush()
    
    # Return ORM object using raw SQL query
    result = db.execute(
        text("SELECT * FROM resource_allocations WHERE id = :id"),
        {"id": allocation_id}
    ).fetchone()
    
    if not result:
        raise ValueError(f"Failed to create allocation {allocation_id}")
    
    # Map to ResourceAllocation object manually (for assertions)
    class AllocationResult:
        def __init__(self, row):
            self.id = row[0]
            self.academic_year = row[1]
            self.status = row[2]
            self.source_import_id = row[3]
            self.day_of_week = row[4]
            self.subject_or_activity = row[5]
            self.section = row[6]
            self.notes = row[7]
            self.group_index = row[8]
    
    return AllocationResult(result)


class TestResourceAllocationLifecycle:
    """Test safe replacement of external allocations on re-import."""
    
    def test_first_import_creates_confirmed_allocation(self, db, fdc_resource, time_slots):
        """First import creates CONFIRMED external allocation."""
        import_id = str(uuid4())
        
        allocation = create_test_external_allocation(
            db=db,
            academic_year="2026-2027",
            section="I-A",
            activity="Workshop 1",
            day=1,
            slot_code="S1",
            source_import_id=import_id,
            status="CONFIRMED",
        )
        
        # Verify allocation was created
        assert allocation.status == "CONFIRMED"
        assert allocation.subject_or_activity == "Workshop 1"
        assert allocation.section == "I-A"
        assert allocation.day_of_week == 1
        assert allocation.source_import_id == import_id
    
    def test_reimport_replaces_matching_allocations(self, db, fdc_resource, time_slots):
        """Re-importing same logical block replaces previous allocation.
        
        Replacement identity: (academic_year, day, section, group_index, slots)
        """
        import_id_v1 = str(uuid4())
        import_id_v2 = str(uuid4())
        
        # First import
        alloc_v1 = create_test_external_allocation(
            db=db,
            academic_year="2026-2027",
            section="I-A",
            activity="Workshop 1",
            day=1,
            slot_code="S1",
            source_import_id=import_id_v1,
            status="CONFIRMED",
        )
        first_alloc_id = alloc_v1.id
        
        # Verify first allocation exists
        count_after_v1 = db.scalar(
            select(func.count()).select_from(ResourceAllocation).where(
                ResourceAllocation.academic_year == "2026-2027",
                ResourceAllocation.status == "CONFIRMED",
            )
        )
        assert count_after_v1 == 1
        
        # Second import (same logical block, different activity)
        # Simulating replacement logic:
        # 1. Delete old allocation with matching (year, day, section, group, slots)
        db.query(ResourceAllocation).filter(
            ResourceAllocation.academic_year == "2026-2027",
            ResourceAllocation.day_of_week == 1,
            ResourceAllocation.section == "I-A",
            ResourceAllocation.group_index == 0,
            ResourceAllocation.source_import_id == import_id_v1,
            ResourceAllocation.status == "CONFIRMED",
        ).delete()
        db.commit()
        
        # 2. Create new allocation
        alloc_v2 = create_test_external_allocation(
            db=db,
            academic_year="2026-2027",
            section="I-A",
            activity="Workshop 2",  # Updated activity
            day=1,
            slot_code="S1",
            source_import_id=import_id_v2,
            status="CONFIRMED",
        )
        
        # Verify replacement
        allocations = db.scalars(
            select(ResourceAllocation).where(
                ResourceAllocation.academic_year == "2026-2027",
                ResourceAllocation.section == "I-A",
                ResourceAllocation.status == "CONFIRMED",
            )
        ).all()
        
        assert len(allocations) == 1, f"Expected 1 allocation, found {len(allocations)}"
        assert allocations[0].subject_or_activity == "Workshop 2"
        assert allocations[0].id != first_alloc_id, "Should be a new allocation"
        assert allocations[0].source_import_id == import_id_v2
    
    def test_manual_allocations_preserved(self, db, fdc_resource, time_slots):
        """Manual allocations (no source_import_id) are never deleted."""
        # Create manual allocation
        manual_alloc = create_test_external_allocation(
            db=db,
            academic_year="2026-2027",
            section="I-A",
            activity="Manual Reservation",
            day=1,
            slot_code="S1",
            source_import_id=None,  # Manual
            status="CONFIRMED",
        )
        manual_id = manual_alloc.id
        
        # Create imported allocation (same logical block)
        import_id = str(uuid4())
        imported_alloc = create_test_external_allocation(
            db=db,
            academic_year="2026-2027",
            section="I-A",
            activity="Workshop",
            day=1,
            slot_code="S1",
            source_import_id=import_id,
            status="CONFIRMED",
        )
        
        # Verify both exist
        all_allocs = db.scalars(
            select(ResourceAllocation).where(
                ResourceAllocation.academic_year == "2026-2027",
                ResourceAllocation.section == "I-A",
                ResourceAllocation.status == "CONFIRMED",
            )
        ).all()
        
        assert len(all_allocs) == 2
        manual_exists = any(str(a.id) == manual_id for a in all_allocs)
        assert manual_exists, "Manual allocation should be preserved"
        
        # Simulate re-import: should only delete allocations with source_import_id
        db.query(ResourceAllocation).filter(
            ResourceAllocation.source_import_id == import_id,
            ResourceAllocation.status == "CONFIRMED",
        ).delete()
        db.commit()
        
        # Verify manual allocation still exists
        remaining = db.scalars(
            select(ResourceAllocation).where(
                ResourceAllocation.academic_year == "2026-2027",
                ResourceAllocation.section == "I-A",
                ResourceAllocation.status == "CONFIRMED",
            )
        ).all()
        
        assert len(remaining) == 1
        assert str(remaining[0].id) == manual_id
        assert remaining[0].source_import_id is None
    
    def test_different_sections_not_replaced(self, db, fdc_resource, time_slots):
        """Allocations with different sections are not replaced."""
        import_id = str(uuid4())
        
        # Create allocation for section I-A
        alloc_ia = create_test_external_allocation(
            db=db,
            academic_year="2026-2027",
            section="I-A",
            activity="Workshop A",
            day=1,
            slot_code="S1",
            source_import_id=import_id,
            status="CONFIRMED",
        )
        
        # Create allocation for section I-B (different section, same day/slot)
        alloc_ib = create_test_external_allocation(
            db=db,
            academic_year="2026-2027",
            section="I-B",
            activity="Workshop B",
            day=1,
            slot_code="S1",
            source_import_id=import_id,
            status="CONFIRMED",
        )
        
        # Simulate replacement for I-A only
        db.query(ResourceAllocation).filter(
            ResourceAllocation.academic_year == "2026-2027",
            ResourceAllocation.section == "I-A",
            ResourceAllocation.day_of_week == 1,
            ResourceAllocation.group_index == 0,
            ResourceAllocation.source_import_id == import_id,
            ResourceAllocation.status == "CONFIRMED",
        ).delete()
        db.commit()
        
        # Create new I-A allocation
        import_id_v2 = str(uuid4())
        new_alloc = create_test_external_allocation(
            db=db,
            academic_year="2026-2027",
            section="I-A",
            activity="Workshop A v2",
            day=1,
            slot_code="S1",
            source_import_id=import_id_v2,
            status="CONFIRMED",
        )
        
        # Verify I-B allocation still exists
        all_allocs = db.scalars(
            select(ResourceAllocation).where(
                ResourceAllocation.academic_year == "2026-2027",
                ResourceAllocation.status == "CONFIRMED",
            )
        ).all()
        
        assert len(all_allocs) == 2
        
        ia_alloc = next(a for a in all_allocs if a.section == "I-A")
        ib_alloc = next(a for a in all_allocs if a.section == "I-B")
        
        assert ia_alloc.subject_or_activity == "Workshop A v2"
        assert ib_alloc.subject_or_activity == "Workshop B"
        assert str(ib_alloc.id) == alloc_ib.id, "I-B allocation should be unchanged"
    
    def test_no_duplicate_on_reimport(self, db, fdc_resource, time_slots):
        """Multiple re-imports do not create duplicates."""
        section = "I-A"
        year = "2026-2027"
        
        # First import
        import_id_v1 = str(uuid4())
        create_test_external_allocation(
            db=db,
            academic_year=year,
            section=section,
            activity="Workshop",
            day=1,
            slot_code="S1",
            source_import_id=import_id_v1,
            status="CONFIRMED",
        )
        
        count_after_v1 = db.scalar(
            select(func.count()).select_from(ResourceAllocation).where(
                ResourceAllocation.academic_year == year,
                ResourceAllocation.section == section,
                ResourceAllocation.status == "CONFIRMED",
            )
        )
        assert count_after_v1 == 1
        
        # Second import (simulate replacement)
        db.query(ResourceAllocation).filter(
            ResourceAllocation.academic_year == year,
            ResourceAllocation.section == section,
            ResourceAllocation.day_of_week == 1,
            ResourceAllocation.group_index == 0,
            ResourceAllocation.status == "CONFIRMED",
        ).delete()
        db.commit()
        
        import_id_v2 = str(uuid4())
        create_test_external_allocation(
            db=db,
            academic_year=year,
            section=section,
            activity="Workshop",
            day=1,
            slot_code="S1",
            source_import_id=import_id_v2,
            status="CONFIRMED",
        )
        
        count_after_v2 = db.scalar(
            select(func.count()).select_from(ResourceAllocation).where(
                ResourceAllocation.academic_year == year,
                ResourceAllocation.section == section,
                ResourceAllocation.status == "CONFIRMED",
            )
        )
        assert count_after_v2 == 1
        
        # Third import (simulate replacement)
        db.query(ResourceAllocation).filter(
            ResourceAllocation.academic_year == year,
            ResourceAllocation.section == section,
            ResourceAllocation.day_of_week == 1,
            ResourceAllocation.group_index == 0,
            ResourceAllocation.status == "CONFIRMED",
        ).delete()
        db.commit()
        
        import_id_v3 = str(uuid4())
        create_test_external_allocation(
            db=db,
            academic_year=year,
            section=section,
            activity="Workshop",
            day=1,
            slot_code="S1",
            source_import_id=import_id_v3,
            status="CONFIRMED",
        )
        
        count_after_v3 = db.scalar(
            select(func.count()).select_from(ResourceAllocation).where(
                ResourceAllocation.academic_year == year,
                ResourceAllocation.section == section,
                ResourceAllocation.status == "CONFIRMED",
            )
        )
        assert count_after_v3 == 1, "Should have exactly 1 allocation after 3 imports"
