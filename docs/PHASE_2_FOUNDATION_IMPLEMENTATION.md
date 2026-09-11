# Phase 2 Foundation — Implementation Summary

**Date**: 2026-09-10  
**Git Baseline**: 6ef827f ("Implement timetable import and availability workflow")  
**Status**: ✅ COMPLETED

## Overview

This document summarizes the implementation of Phase 2 Foundation for the Teacher Availability System. This foundation establishes the shared canonical domain model that will be used by XLSX import, future DOCX import, direct editing, validation, and persistence.

## Objective

Establish a **source-agnostic canonical domain model** that:
- Can be produced by XLSX, DOCX, or editor UI
- Provides structured validation feedback
- Preserves source location traceability
- Maintains teacher identity stability
- Represents logical schedule activities consistently

## Architectural Boundary Introduced

### Before (Phase 1)
```
XLSX → parser → normalizer → validator → ImportPreview → persister → DB
```

### After (Phase 2 Foundation)
```
XLSX → parser → normalizer → validator → ImportPreview
                                              ↓
                                      CanonicalTimetable (staged)
                                              ↓
                                        ImportPreview (API response)
                                              ↓
                                         persister → DB
```

### Future (Phase 2+ Complete)
```
DOCX ─┐
XLSX ─┼──> CanonicalTimetable ──> ValidationEngine ──> persist ──> DRAFT ──> CONFIRMED
Editor ┘
```

## Files Created

### 1. Domain Model Core
- **`backend/app/domain/__init__.py`**: Package exports
- **`backend/app/domain/timetable.py`**: Core domain types
  - `SourceLocation`: Immutable reference (XLSX/DOCX/MANUAL support)
  - `ValidationSeverity`: ERROR/WARNING/INFO enum
  - `ValidationIssue`: Structured validation feedback
  - `TeacherIdentity`: Stable teacher reference
  - `ActivitySlotRange`: Day + slot occupancy
  - `ScheduleActivity`: One logical activity
  - `CanonicalTimetable`: Complete timetable representation

### 2. Converters
- **`backend/app/domain/converters.py`**: Bidirectional conversion
  - `import_preview_to_canonical()`: ImportPreview → CanonicalTimetable
  - `canonical_to_import_preview()`: CanonicalTimetable → ImportPreview

### 3. Tests
- **`backend/tests/test_canonical_domain.py`**: Comprehensive domain model tests
  - 18 test cases covering all domain types
  - Source location traceability (XLSX/DOCX/MANUAL)
  - Validation issue severity levels
  - Teacher identity stability
  - Multi-slot activities (LAB spanning S4+S5)
  - Bidirectional conversion roundtrip
  - Error detection and propagation

### 4. Documentation
- **`backend/app/domain/README.md`**: Complete domain model documentation
  - Purpose and architecture
  - Core type descriptions with examples
  - Usage patterns
  - Design decisions and rationale
  - Migration notes
  - Future extension guidance

## Files Modified

### `backend/app/api/imports.py`

**Changes**:
1. Added imports for domain model and converters
2. Changed staging storage type: `_STAGING: dict[str, CanonicalTimetable]` (was `ImportPreview`)
3. Updated `upload_excel()`: Convert ImportPreview to CanonicalTimetable before staging
4. Updated `get_import()`: Convert staged CanonicalTimetable to ImportPreview for response
5. Updated `confirm_import()`: Convert staged CanonicalTimetable to ImportPreview for validation

**Design Note**: API contracts unchanged. All endpoints still accept/return ImportPreview for backward compatibility. CanonicalTimetable is stored internally.

**Comment Added**:
```python
# DESIGN NOTE: We store the canonical representation, not ImportPreview.
# This allows future DOCX/Editor sources to use the same staging mechanism.
# ImportPreview is derived on-demand for API responses.
```

## Test Results

### New Tests
```
tests/test_canonical_domain.py: 18 passed (0.03s)
```

### Regression Tests (All Passing)
```
tests/test_excel_import.py: 60 passed, 1 skipped
tests/test_excel_import_integration.py: 7 passed
tests/test_excel_import_contract.py: 15 passed
tests/test_timetable.py: 39 passed
tests/test_timetable_confirm.py: 30 passed
tests/test_timetable_confirm_integration.py: 22 passed
tests/test_integration.py: All passed

TOTAL: 191 passed, 1 skipped (42.74s)
```

**Conclusion**: No existing functionality broken. All Phase 1 XLSX import behavior preserved.

## Design Decisions

### 1. CanonicalTimetable in Staging (Not ImportPreview)

**Decision**: Store `CanonicalTimetable` in `_STAGING`, derive `ImportPreview` on demand

**Rationale**:
- Staging represents the source of truth for in-progress imports
- ImportPreview is a transport/UI format, not the domain model
- Future DOCX/Editor sources will produce CanonicalTimetable directly
- Avoids needing separate staging stores for different sources
- Aligns with architecture document principle: "canonical is authoritative"

### 2. Teacher Reference by Acronym (Not Embedded Identity)

**Decision**: Activities reference teachers via `acronym: str`, not embedded `TeacherIdentity`

**Rationale**:
- Avoids duplicating teacher data across activities
- Prevents conflicting teacher information (e.g., same acronym with different names)
- Centralizes teacher data in `timetable.teachers[]`
- One teacher can have multiple activities without data duplication
- Mirrors database structure (teachers table + foreign keys)

### 3. Frozen SourceLocation

**Decision**: `@dataclass(frozen=True)` on `SourceLocation`

**Rationale**:
- Source references should never change after creation
- Immutability prevents accidental modification
- Signals semantic intent: "this is a reference, not mutable state"
- Enables use as dict keys if needed in future

### 4. Python Dataclasses (Not Pydantic)

**Decision**: Use Python `dataclasses` for domain types

**Rationale**:
- Simpler and sufficient for domain objects
- No serialization/validation framework overhead at domain layer
- Pydantic remains for API boundaries (ImportPreview, schemas)
- Keeps domain layer lightweight and framework-agnostic
- Easy to test without framework dependencies

### 5. Preserve Existing Validator/Persister Interfaces

**Decision**: Convert CanonicalTimetable → ImportPreview for existing validator/persister

**Rationale**:
- Minimizes scope of changes (foundation only)
- Existing validation logic already works and is tested
- Avoids rewriting validator/persister in this step
- Future ValidationEngine will consume CanonicalTimetable directly
- Pragmatic: working code continues working

## Source Location Preservation

The implementation preserves source location information through the conversion boundary:

### XLSX Sheet/Row Reference
```python
# ImportPreview.teachers[].row_ref = "Teachers!2"
# ImportPreview.days["monday"][].row_refs = ["Schedule!15"]

# Becomes:
SourceLocation(
    source_type="XLSX",
    sheet_name="Schedule",
    row_number=15,
)
```

### Display Format
```python
loc.to_display()
# "XLSX: sheet 'Schedule', row 15"
```

### Future DOCX Support (Ready)
```python
SourceLocation(
    source_type="DOCX",
    table_index=2,
    table_row=5,
    table_col=3,
)
# Display: "DOCX: table 2, row 5, col 3"
```

## Domain Rules Representation

The canonical model represents (but does not yet enforce) these rules:

### Time Slots
- Working slots: S1–S9
- Breaks: 10:45–11:15 (morning), 13:05–14:00 (lunch)
- Breaks are NOT working slots

### Activity Duration
- LAB: Exactly 2 consecutive working slots
- CLASS/OTHER: 1 or 2 consecutive working slots
- 3+ consecutive slots: Invalid

### Schedule Constraints
- Sunday (day_of_week=7): Invalid
- Slot overlap: Same teacher cannot occupy same slot twice on same day

**Note**: Validation rules are not enforced by the domain model itself. They will be enforced by the ValidationEngine (next implementation step).

## Key Behaviors Preserved

### ONE Logical Activity = Multiple Slot Mappings

**Example**: LAB "DBMS Lab" on Thursday S4+S5

**Domain Model**:
```python
ScheduleActivity(
    teacher_acronym="RAJ",
    entry_type="LAB",
    subject_or_activity="DBMS Lab",
    slot_range=ActivitySlotRange(
        day_of_week=4,  # Thursday
        slot_codes=["S4", "S5"]
    )
)
```

**Database** (unchanged):
```sql
-- ONE schedule_entry
INSERT INTO schedule_entries (entry_type, subject_or_activity, ...)
VALUES ('LAB', 'DBMS Lab', ...);

-- TWO schedule_entry_slots
INSERT INTO schedule_entry_slots (schedule_entry_id, time_slot_id)
VALUES (entry_id, S4_uuid), (entry_id, S5_uuid);
```

### DRAFT/CONFIRMED Semantics

- Import always creates DRAFT timetables
- Existing CONFIRMED timetables never touched by import
- Existing DRAFT timetables replaced on re-import
- Teacher creates/reuses logic unchanged

### Transaction Safety

- All changes atomic (commit/rollback)
- Validation blocks confirmation on ERROR-level issues
- Staging is in-memory (lost on restart, by design)

## API Contract Preservation

All API endpoints **unchanged**:

### POST /api/v1/imports/excel
- Request: `multipart/form-data` (file + academic_year)
- Response: `ImportPreview` (same schema)
- Behavior: Upload → parse → normalize → validate → stage → return preview

### GET /api/v1/imports/{import_id}
- Response: `ImportPreview` (same schema)
- Behavior: Retrieve staged import (now converts CanonicalTimetable → ImportPreview)

### POST /api/v1/imports/{import_id}/confirm
- Response: `ImportConfirmOut` (same schema)
- Behavior: Re-validate → persist → commit → return result

### DELETE /api/v1/imports/{import_id}
- Behavior: Discard staged import (no change)

**Frontend Impact**: **NONE**. All API contracts preserved.

## Future Integration Points

### DOCX Import (Next Step)

```python
# 1. Parse DOCX
docx_data = parse_docx_timetable(file_bytes)

# 2. Convert directly to canonical
canonical = docx_to_canonical(docx_data, academic_year)

# 3. Stage (same staging store)
_STAGING[canonical.import_id] = canonical

# 4. Use existing conversion for API response
return canonical_to_import_preview(canonical)
```

### Direct Editing (Future)

```python
# 1. UI sends edit request
edit = {"teacher": "RAJ", "day": "monday", "slot": "S4", "action": "add_class"}

# 2. Convert to canonical activity
activity = edit_to_canonical_activity(edit)

# 3. Validate
issues = validate_activity(activity, timetable, db)

# 4. Apply if valid
if not has_errors(issues):
    timetable.activities.append(activity)
```

### Unified ValidationEngine (Phase 2 Next)

```python
def validate_canonical(canonical: CanonicalTimetable, db: Session) -> CanonicalTimetable:
    """
    Validate canonical timetable against domain rules and DB state.
    
    Returns updated canonical with ValidationIssues populated.
    """
    # Validate teacher references
    validate_teachers(canonical, db)
    
    # Validate slot rules
    validate_slot_rules(canonical)
    
    # Validate overlaps
    validate_no_overlaps(canonical)
    
    # Validate against existing timetables
    validate_conflicts(canonical, db)
    
    return canonical
```

## Known Limitations

### 1. Department Not Yet Normalized

Teacher department is a free-text string. Future work:
- Normalize department names
- Add department reference table
- Enforce unique (acronym, department) instead of global acronym uniqueness

### 2. Validator Still Uses ImportPreview

Current validator interface: `resolve_and_validate(preview: ImportPreview, db: Session)`

Future: `validate_canonical(canonical: CanonicalTimetable, db: Session) -> CanonicalTimetable`

### 3. No Resource Representation Yet

Resources (classrooms, labs) not yet in domain model. Future:
- Add `ResourceReference` to domain model
- Add resource validation rules
- Add resource availability checking

### 4. No Bulk Publishing Yet

Canonical model supports it conceptually, but implementation not in this step.

## Migration Safety

### Breaking Changes
**None**. This is an additive change.

### Modified Behavior
- Staging now stores `CanonicalTimetable` instead of `ImportPreview`
- API responses remain `ImportPreview` (no change to external contracts)

### Backward Compatibility
- All existing XLSX imports work identically
- All existing tests pass without modification
- Database schema unchanged
- Frontend unchanged
- Existing API clients unaffected

## Conclusion

Phase 2 Foundation successfully implemented:

✅ Canonical domain model established  
✅ Source location traceability (XLSX/DOCX/MANUAL)  
✅ Structured validation framework (ERROR/WARNING/INFO)  
✅ Teacher identity stability (no duplication)  
✅ Logical activity representation (multi-slot support)  
✅ Bidirectional conversion (ImportPreview ↔ CanonicalTimetable)  
✅ XLSX import integration complete  
✅ All existing tests passing (191 passed, 1 skipped)  
✅ No breaking changes  
✅ Comprehensive test coverage (18 new tests)  
✅ Complete documentation  

**Ready for**: DOCX parser implementation, ValidationEngine implementation, Resource model additions, Direct editing integration.

**No issues or concerns**.
