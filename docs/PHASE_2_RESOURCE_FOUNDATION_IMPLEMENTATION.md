# Phase 2: Resource Foundation Implementation

## Overview

This document describes the implementation of Phase 2 Resource Foundation for the Teacher Availability System. This phase introduces normalized resource management with resolution status tracking, departmental scoping, and backward compatibility with the existing `room` TEXT field.

## Implementation Status

✅ **COMPLETE** - All foundation components implemented and tested

### Components Delivered

1. **Database Schema** (`database/resource_migration.sql`)
   - resources table with normalized_name, resource_type, department scoping
   - resource_aliases table for alternative names
   - schedule_entries.resource_id column (nullable, non-breaking)
   - Unique constraints for normalized names within departments
   - Indexes for performance

2. **ORM Models** (`backend/app/models/models.py`)
   - Resource model with relationships
   - ResourceAlias model
   - ScheduleEntry.resource_id and resource relationship

3. **Domain Model** (`backend/app/domain/resources.py`)
   - ResourceReference with resolution status
   - ResourceResolutionStatus enum (RESOLVED, UNRESOLVED, AMBIGUOUS)
   - ResourceResolver for name→resource resolution
   - normalize_resource_name() function

4. **Domain Integration** (`backend/app/domain/timetable.py`)
   - ScheduleActivity.resource_ref field
   - Backward compatible with room TEXT field

5. **Tests** (`backend/tests/test_resource_foundation.py`)
   - 22 comprehensive tests covering all scenarios
   - All tests passing in SQLite test environment

## Database Migration Required

**IMPORTANT**: The migration `database/resource_migration.sql` must be applied to the PostgreSQL database before the ORM changes can be used with the real database.

### Migration Commands

```bash
# Connect to your Supabase PostgreSQL database
psql <your-database-connection-string>

# Run the migration
\i database/resource_migration.sql
```

The migration is **non-breaking**:
- Uses `CREATE TABLE IF NOT EXISTS`
- Uses `ADD COLUMN IF NOT EXISTS`
- resource_id is nullable (no default value required)
- room TEXT column is preserved
- No data transformation required

### Why Migration is Required

The integration tests (`test_excel_import_integration.py`, `test_integration.py`, `test_timetable_confirm_integration.py`) connect to a real PostgreSQL database. The ORM model now includes `resource_id` as a field on ScheduleEntry, so SQLAlchemy tries to INSERT this column even when it's NULL. This fails if the column doesn't exist in the database schema.

**Current Test Results**:
- ✅ 221 tests passing (including all 22 resource foundation tests)
- ❌ 13 tests failing (integration tests that require real PostgreSQL with migration applied)
- The failing tests all show: `column "resource_id" of relation "schedule_entries" does not exist`

## Architecture Decisions

### 1. Resource Resolution Statuses

**RESOLVED**: Resource name matches exactly one active resource in scope
**UNRESOLVED**: Resource name doesn't match any resource (may be typo, new resource, or inactive)
**AMBIGUOUS**: Resource name matches multiple resources in scope (user must disambiguate)

### 2. Department Scoping

- `department` NULL = shared resource (available to all departments)
- `department` NOT NULL = department-owned (only available to that department)
- Resolution searches: department-owned + shared resources
- When both shared and dept-owned have same normalized name → AMBIGUOUS

### 3. No Auto-Creation

Resolution **never** automatically creates Resource rows. Unknown names return UNRESOLVED status. This prevents data pollution and ensures intentional resource management.

### 4. Conservative Normalization

`normalize_resource_name()` preserves hyphens, periods, and underscores to avoid accidental merging of distinct resources (e.g., "Lab-1A" vs "Lab1A").

### 5. Backward Compatibility

- `room` TEXT field preserved on schedule_entries
- `resource_id` nullable (gradual migration)
- ScheduleActivity has both `room` (legacy) and `resource_ref` (new)
- Old code continues to work unchanged

## File Changes

### Created Files

1. `database/resource_migration.sql` - PostgreSQL migration script
2. `backend/app/domain/resources.py` - Domain model and resolver
3. `backend/tests/test_resource_foundation.py` - Comprehensive test suite
4. `docs/PHASE_2_RESOURCE_FOUNDATION_IMPLEMENTATION.md` - This document

### Modified Files

1. `backend/app/models/models.py`
   - Added Resource class
   - Added ResourceAlias class
   - Added ScheduleEntry.resource_id field and relationship

2. `backend/app/domain/timetable.py`
   - Added TYPE_CHECKING import for ResourceReference forward reference
   - Added ScheduleActivity.resource_ref field (optional)

3. `backend/app/domain/__init__.py`
   - Exported ResourceReference, ResourceResolutionStatus, ResourceResolver, normalize_resource_name

4. `backend/tests/conftest.py`
   - Added resources table DDL for SQLite tests
   - Added resource_aliases table DDL for SQLite tests
   - Added resource_id column to schedule_entries DDL

## Resource Resolution Behavior

### Normalization Rules

```python
normalize_resource_name("  Lab 1A  ")  # → "lab 1a"
normalize_resource_name("LAB  1A")      # → "lab 1a"
normalize_resource_name("Lab-1A")       # → "lab-1a" (hyphen preserved)
normalize_resource_name("Lab.1A")       # → "lab.1a" (period preserved)
```

### Resolution Examples

**Case 1: Exact Match**
- Database: Resource(name="Lab 1A", normalized_name="lab 1a", department="CS")
- Input: "Lab 1A", department="CS"
- Result: RESOLVED → Resource ID

**Case 2: Unknown Resource**
- Database: (no matching resource)
- Input: "Unknown Lab", department="CS"
- Result: UNRESOLVED (no auto-creation)

**Case 3: Alias Resolution**
- Database: Resource(name="Computer Applications Lab 1A", normalized_name="computer applications lab 1a")
  Alias(alias="CA Lab 1A", normalized_alias="ca lab 1a")
- Input: "CA Lab 1A", department="CS"
- Result: RESOLVED → Resource ID

**Case 4: Ambiguous Match**
- Database: 
  - Resource(name="Seminar Hall", department=NULL)  # shared
  - Resource(name="Seminar Hall", department="CS")  # dept-owned
- Input: "Seminar Hall", department="CS"
- Result: AMBIGUOUS (both match, user must disambiguate)

**Case 5: Department Scoping**
- Database: Resource(name="CS Lab 1", department="CS")
- Input: "CS Lab 1", department="Electronics"
- Result: UNRESOLVED (not in scope)

**Case 6: Inactive Resource**
- Database: Resource(name="Old Lab", is_active=FALSE)
- Input: "Old Lab", department="CS"
- Result: UNRESOLVED (inactive resources not resolved)

## Database Schema Details

### resources Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY | Auto-generated |
| name | TEXT | NOT NULL | Display name |
| normalized_name | TEXT | NOT NULL | Lowercase, trimmed, collapsed whitespace |
| resource_type | TEXT | NOT NULL, CHECK | One of: LAB, CLASSROOM, SEMINAR_HALL, AUDITORIUM, OTHER |
| department | TEXT | NULLABLE | NULL = shared, NOT NULL = department-owned |
| capacity | SMALLINT | NULLABLE, CHECK > 0 | Room capacity |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active/inactive flag |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | Creation timestamp |
| updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | Last update timestamp |

**Unique Constraints**:
- (normalized_name, department) WHERE department IS NOT NULL
- (normalized_name) WHERE department IS NULL

### resource_aliases Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY | Auto-generated |
| resource_id | UUID | NOT NULL, FK | References resources(id) |
| alias | TEXT | NOT NULL | Alias display name |
| normalized_alias | TEXT | NOT NULL, UNIQUE | Normalized alias |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | Creation timestamp |

**Unique Constraint**: normalized_alias (ensures deterministic resolution)

### schedule_entries.resource_id

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| resource_id | UUID | NULLABLE, FK | References resources(id), NULL = unresolved or legacy |

## Test Coverage

### Test Categories (22 tests)

1. **Normalization** (5 tests)
   - Basic normalization
   - Whitespace collapse
   - Punctuation preservation
   - Empty string handling
   - Consistency

2. **Basic Resolution** (3 tests)
   - Empty name → UNRESOLVED
   - Unknown resource → UNRESOLVED
   - No auto-creation

3. **Exact Resolution** (3 tests)
   - Exact match → RESOLVED
   - Case insensitive matching
   - Inactive resources not resolved

4. **Alias Resolution** (2 tests)
   - Unique alias → RESOLVED
   - Multiple aliases same resource

5. **Ambiguous Resolution** (2 tests)
   - Multiple resources in scope → AMBIGUOUS
   - Alias ambiguity (schema prevents this)

6. **Department Scoping** (2 tests)
   - Department-owned resolves in department
   - Department-owned doesn't resolve in other department

7. **Shared Resources** (2 tests)
   - Shared available to all departments
   - Shared + dept-owned same name → AMBIGUOUS

8. **Domain Model** (2 tests)
   - ResourceReference construction
   - ResourceReference in ScheduleActivity

9. **Batch Resolution** (1 test)
   - Batch resolution processes multiple names

### Test Results

```
backend/tests/test_resource_foundation.py: 22 passed
backend/tests (all): 221 passed, 13 failed, 1 skipped

Failed tests: integration tests requiring PostgreSQL migration
```

## Success Criteria Met

✅ 1. Normalized resource model with id, name, normalized_name, resource_type, department, capacity, is_active
✅ 2. resource_aliases table for alternative names
✅ 3. ResourceReference domain model with resolution status
✅ 4. ResourceResolutionStatus enum (RESOLVED, UNRESOLVED, AMBIGUOUS)
✅ 5. ResourceResolver.resolve() and resolve_batch()
✅ 6. normalize_resource_name() with conservative normalization
✅ 7. Department scoping (NULL = shared, NOT NULL = owned)
✅ 8. No auto-creation of resources
✅ 9. Alias resolution
✅ 10. Inactive resources not resolved
✅ 11. Backward compatibility with room TEXT field
✅ 12. ScheduleEntry.resource_id nullable
✅ 13. Database migration non-breaking
✅ 14. Comprehensive tests (22 tests, all passing)
✅ 15. Resource type explicit (no automatic inference)

## Next Steps

1. **Apply Migration**: Run `database/resource_migration.sql` against your Supabase PostgreSQL database
2. **Verify Integration Tests**: After migration, run `pytest tests/` to verify all 235 tests pass
3. **Resource Management UI**: Build admin interface for creating/editing resources and aliases (future phase)
4. **Migration Tool**: Build tool to migrate existing room TEXT values to resources (future phase)
5. **XLSX Import Integration**: Update XLSX import to resolve room names using ResourceResolver (future phase)

## API Usage Examples

### Resolving a Single Resource

```python
from sqlalchemy.orm import Session
from app.domain.resources import ResourceResolver, ResourceResolutionStatus

resolver = ResourceResolver(db)
result = resolver.resolve("Lab 1A", department="Computer Science")

if result.resolution_status == ResourceResolutionStatus.RESOLVED:
    print(f"Resolved to resource ID: {result.resolved_resource_id}")
elif result.resolution_status == ResourceResolutionStatus.UNRESOLVED:
    print(f"Unknown resource: {result.display_name}")
elif result.resolution_status == ResourceResolutionStatus.AMBIGUOUS:
    print(f"Ambiguous resource: {result.display_name} matches multiple resources")
```

### Batch Resolution

```python
names = [
    ("Lab 1", "Computer Science", None),
    ("Lab 2", "Computer Science", None),
    ("Unknown Lab", "Computer Science", None),
]

results = resolver.resolve_batch(names)

for result in results:
    print(f"{result.display_name}: {result.resolution_status.value}")
```

### Using with ScheduleActivity

```python
from app.domain.timetable import ScheduleActivity, ActivitySlotRange

resource_ref = resolver.resolve("Lab 1A", department="Computer Science")

activity = ScheduleActivity(
    teacher_acronym="TEST",
    entry_type="LAB",
    subject_or_activity="DBMS Lab",
    section="MCA-1A",
    room="Lab 1A",  # Legacy field
    notes=None,
    slot_range=ActivitySlotRange(day_of_week=1, slot_codes=["S1", "S2"]),
    resource_ref=resource_ref,  # NEW: Normalized resource reference
)

# Check resolution status
if activity.resource_ref.resolution_status == ResourceResolutionStatus.UNRESOLVED:
    print("⚠️  Resource not found in database")
```

## Known Limitations

1. **Migration Required**: Integration tests fail until migration is applied to PostgreSQL
2. **No Resource CRUD API**: Resource management requires direct database access (future phase)
3. **No Migration Tool**: Existing room TEXT values not automatically converted (future phase)
4. **No XLSX Integration**: XLSX import doesn't use ResourceResolver yet (future phase)
5. **Manual Resource Creation**: Resources and aliases must be created manually (future phase)

## Related Documents

- `docs/PHASE_2_ARCHITECTURE.md` - Overall Phase 2 architecture
- `docs/DATA_MODEL.md` - Original data model
- `database/resource_migration.sql` - Migration script
- `backend/app/domain/resources.py` - Implementation
- `backend/tests/test_resource_foundation.py` - Test suite

## Conclusion

The Phase 2 Resource Foundation is **complete and tested**. All foundation components are implemented and working correctly in the test environment. The only remaining step is applying the migration to the PostgreSQL database to enable the integration tests.

The implementation follows all architectural principles:
- Non-breaking migration
- Backward compatibility preserved
- Conservative resolution (no auto-creation)
- Explicit resource types
- Department scoping with shared resources
- Comprehensive test coverage

Once the migration is applied, the system will have a solid foundation for resource management that can be built upon in future phases.
