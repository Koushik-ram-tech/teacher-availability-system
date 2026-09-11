# ValidationEngine Implementation — Step 2.5 Complete ✅

**Date**: 2026-09-10  
**Git Baseline**: 6ef827f ("Implement timetable import and availability workflow")  
**Status**: ✅ COMPLETED

## Overview

Successfully implemented the real ValidationEngine and rewired the XLSX import pipeline to make CanonicalTimetable the actual validation boundary, removing the transitional bridge to the legacy validator.

## Objective Achieved

✅ CanonicalTimetable is now the **actual semantic validation input**  
✅ ValidationEngine operates directly on canonical domain model  
✅ Legacy validator (resolve_and_validate) is no longer used in the import flow  
✅ ImportPreview remains **transport/API representation only**  
✅ No duplicate conflicting business validation exists  

## Files Created

### 1. ValidationEngine Core
**`backend/app/domain/validation.py`** (793 lines)
- Complete source-agnostic validation engine
- Validates CanonicalTimetable against domain rules and DB state
- Populates structured ValidationIssue objects
- Does NOT modify canonical structure

### 2. Comprehensive Tests
**`backend/tests/test_validation_engine.py`** (826 lines)
- 21 comprehensive test cases
- Covers all validation rules
- Tests source location preservation
- Tests overlap detection
- Tests duplicate detection

## Files Modified

### 1. Import API (`backend/app/api/imports.py`)
**Changes**:
- Removed import of legacy `resolve_and_validate`
- Added import of `validate_canonical`
- Rewired upload flow: `normalize()` → `import_preview_to_canonical()` → `validate_canonical()` → stage → return
- Rewired confirm flow: retrieve canonical → `validate_canonical()` → check errors → persist
- Uses `canonical.has_errors()` instead of `preview.errors`

**Before**:
```python
preview = normalize(raw)
preview = resolve_and_validate(preview, db)  # Legacy validator
canonical = import_preview_to_canonical(preview)
_STAGING[canonical.import_id] = canonical
return preview
```

**After**:
```python
preview = normalize(raw)
canonical = import_preview_to_canonical(preview)
canonical = validate_canonical(canonical, db)  # ValidationEngine
_STAGING[canonical.import_id] = canonical
return canonical_to_import_preview(canonical)
```

### 2. Domain Package (`backend/app/domain/__init__.py`)
**Changes**:
- Added `validate_canonical` to exports

### 3. Canonical Converter (`backend/app/domain/converters.py`)
**Changes**:
- Updated `canonical_to_import_preview()` to collect ERROR-level issues from teachers and activities into global errors list
- Ensures validation errors are surfaced in API responses

## Architectural Boundary Established

### Current XLSX Flow (Implemented)
```
XLSX bytes
    ↓
parse_workbook() [structural parsing]
    ↓
normalize() [syntax + basic rules, produces ImportPreview with errors/warnings]
    ↓
import_preview_to_canonical() [convert to domain model, preserve parse errors]
    ↓
validate_canonical() [DOMAIN VALIDATION ENGINE] ← **NEW AUTHORITATIVE BOUNDARY**
    ↓
canonical_to_import_preview() [derive transport format]
    ↓
[API response: ImportPreview]
    ↓
[Stage: CanonicalTimetable]
```

### Confirm Flow
```
Retrieve CanonicalTimetable from staging
    ↓
validate_canonical(canonical, db) [re-validate against current DB state]
    ↓
canonical.has_errors() ? → block : continue
    ↓
canonical_to_import_preview() [for persister compatibility]
    ↓
persist_import(preview, db)
```

## ValidationEngine Rules Implemented

### Academic Year Validation
- ✅ **INVALID_ACADEMIC_YEAR**: Format must be YYYY-YYYY with consecutive years

### Teacher Validation
- ✅ **TEACHER_MISSING_NAME**: Name is required
- ✅ **TEACHER_MISSING_ACRONYM**: Acronym is required
- ✅ **TEACHER_INVALID_LEVEL**: Level must be 'UG' or 'PG'
- ✅ **TEACHER_INVALID_SEMESTER**: Semester must be positive integer
- ✅ **TEACHER_DUPLICATE_IDENTITY**: No duplicate acronyms within timetable
- ✅ **TEACHER_UNKNOWN_PROGRAM**: Program must exist in DB
- ✅ **TEACHER_CONFLICT**: Existing teacher with same acronym but different identity
- ✅ **TEACHER_NO_ACTIVITIES**: Info-level flag for teachers with no schedule (INFO severity)
- ✅ **ACTIVITY_UNKNOWN_TEACHER**: Activity references non-existent teacher

### Schedule Validation
- ✅ **SCHEDULE_SUNDAY**: Sunday is non-working day
- ✅ **SCHEDULE_INVALID_DAY**: Day must be 1-7 (ISO weekday)
- ✅ **SCHEDULE_INVALID_SLOT**: Slot code must be S1-S9
- ✅ **SCHEDULE_LAB_INVALID_DURATION**: LAB must be exactly 2 slots
- ✅ **SCHEDULE_LAB_NON_CONSECUTIVE**: LAB slots must be consecutive with no break
- ✅ **SCHEDULE_INVALID_DURATION**: Activity cannot span 3+ slots
- ✅ **SCHEDULE_NON_CONSECUTIVE**: CLASS/OTHER with 2 slots must be consecutive

### Overlap Detection
- ✅ **TEACHER_OVERLAP**: Same teacher, same day, same slot, different activities
- ✅ **Correct handling**: Single LAB spanning S4+S5 does NOT self-overlap

### Duplicate Detection
- ✅ **SCHEDULE_DUPLICATE_ACTIVITY**: Exact duplicate activities detected

### Source Location Preservation
- ✅ Validation issues include source locations where available
- ✅ XLSX sheet/row information preserved through validation
- ✅ Ready for DOCX source locations (table/row/col)

## Legacy Validator Status

**`backend/app/services/excel_import/validator.py`**:
- **Status**: NO LONGER USED in import flow
- **Function**: `resolve_and_validate(preview: ImportPreview, db: Session)`
- **Superseded by**: `validate_canonical(canonical: CanonicalTimetable, db: Session)`
- **File preserved**: Yes (may be useful for reference/migration)
- **Decision**: Keep file temporarily but not imported anywhere

The DB-level validation logic (program resolution, teacher conflict detection) has been **moved into ValidationEngine** as `_validate_teacher_db_resolution()`.

## Test Results

### New ValidationEngine Tests
```bash
tests/test_validation_engine.py: 21 passed (0.03s)

Breakdown:
- TestValidationEngineBasics: 3 tests
- TestTeacherValidation: 6 tests  
- TestScheduleValidation: 7 tests
- TestOverlapDetection: 2 tests
- TestDuplicateDetection: 1 test
- TestSourceLocationPreservation: 1 test
- TestMultipleIssues: 1 test
```

### Full Test Suite
```bash
Total: 212 passed, 1 skipped (102.57s)

Including:
- test_canonical_domain.py: 18 passed
- test_validation_engine.py: 21 passed
- test_excel_import.py: 60 passed, 1 skipped
- test_excel_import_integration.py: 7 passed
- test_excel_import_contract.py: 15 passed
- test_timetable.py: 39 passed
- test_timetable_confirm.py: 30 passed
- test_timetable_confirm_integration.py: 22 passed
```

**Conclusion**: All existing XLSX import functionality preserved. No regressions.

## Key Design Decisions

### 1. ValidationEngine Does NOT Clear Pre-existing Issues

**Decision**: ValidationEngine adds new issues but does NOT clear issues from parser/normalizer

**Rationale**:
- Parser/normalizer errors (invalid times, malformed data) must flow through
- Validation should be additive, not destructive
- Allows multi-stage validation (parse → normalize → validate)

**Implementation**:
```python
def validate_canonical(canonical: CanonicalTimetable, db: Session) -> CanonicalTimetable:
    # Do NOT clear existing issues - they may come from parser/normalizer
    # We only ADD new validation issues
    
    _validate_academic_year(canonical)
    _validate_teachers(canonical)
    # ... etc
```

### 2. Error Collection in canonical_to_import_preview

**Decision**: Collect ERROR-level issues from all sources (global, teachers, activities)

**Rationale**:
- ImportPreview.errors is a flat list for API compatibility
- Validation errors at any level must block confirmation
- Frontend expects errors in single array

**Implementation**:
```python
# Convert global issues
errors = [issue.message for issue in canonical.global_issues if issue.severity == ValidationSeverity.ERROR]

# Collect ERROR-level issues from teachers and activities
for teacher in canonical.teachers:
    for issue in teacher.issues:
        if issue.severity == ValidationSeverity.ERROR:
            errors.append(issue.message)

for activity in canonical.activities:
    for issue in activity.issues:
        if issue.severity == ValidationSeverity.ERROR:
            errors.append(issue.message)
```

### 3. Multi-Slot Activity Self-Overlap Check

**Decision**: Use activity object identity, not slot matching, for overlap detection

**Rationale**:
- A single LAB spanning S4+S5 should NOT be flagged as overlapping with itself
- Overlap means different logical activities competing for same slot
- Build occupancy map: `(teacher, day, slot) → [activities]`, then check `len(activities) > 1`

### 4. DB Validation Integrated into ValidationEngine

**Decision**: Move program resolution and teacher conflict detection into ValidationEngine

**Rationale**:
- These are domain validation rules, not transport-specific
- ValidationEngine should be the single authoritative validation boundary
- Avoids having validation split across multiple modules

## Behaviors Preserved

### 1. ONE Logical Activity = Multiple Slot Mappings ✅
- LAB "DBMS Lab" Thursday S4+S5 = ONE ScheduleActivity with slot_codes=["S4", "S5"]
- Database: ONE schedule_entry, TWO schedule_entry_slots
- No change to persistence logic

### 2. DRAFT/CONFIRMED Semantics ✅
- Import always creates DRAFT
- Never touches CONFIRMED
- Validation runs on both upload and confirm

### 3. Transaction Safety ✅
- All validation errors block confirmation
- Rollback on any persistence failure
- Atomic create/reuse of teachers and timetables

### 4. API Contracts ✅
- POST /api/v1/imports/excel → ImportPreview
- GET /api/v1/imports/{import_id} → ImportPreview
- POST /api/v1/imports/{import_id}/confirm → ImportConfirmOut
- All response schemas unchanged

## ImportPreview Usage

**Current Role**: Transport/API representation ONLY

**Produced by**:
- `canonical_to_import_preview(canonical)` for API responses

**Consumed by**:
- API clients (frontend)
- `persist_import(preview, db)` - temporary compatibility layer

**Future**:
- Persister will be updated to accept CanonicalTimetable directly
- ImportPreview will remain only for API responses
- No business logic should depend on ImportPreview

## Known Limitations & Future Work

### 1. Persister Still Uses ImportPreview
**Current**: `persist_import(preview: ImportPreview, db: Session)`  
**Future**: `persist_canonical(canonical: CanonicalTimetable, db: Session)`  
**Impact**: Minimal - conversion is cheap, logic is sound

### 2. Department Not Normalized
**Current**: Department is free text  
**Future**: Department reference table, normalized lookups  
**Phase**: Phase 2 later steps

### 3. No Resource Validation Yet
**Current**: Room/lab fields are free text  
**Future**: Resource reference validation, availability checks  
**Phase**: Phase 2 later steps

### 4. No Bulk Validation Yet
**Current**: Validates one import at a time  
**Future**: Cross-timetable validation for bulk publish  
**Phase**: Phase 3

## Architectural Concerns Addressed

### ✅ Single Source of Truth for Validation
- **Before**: Normalizer had some rules, validator had others, persister had structural checks
- **After**: ValidationEngine is authoritative for domain rules

### ✅ Source-Agnostic Validation
- **Ready for**: DOCX import, direct editing, bulk operations
- **Uses**: CanonicalTimetable (not ImportPreview)

### ✅ Structured Error Reporting
- **Before**: String messages in arrays
- **After**: ValidationIssue with severity, code, message, source_locations, affected_entities, suggestions

### ✅ No Duplicate Rule Implementations
- **Before**: LAB validation in normalizer, might need similar in validator
- **After**: All domain rules in ValidationEngine only

## Next Steps Enabled

This implementation enables:

1. **DOCX Import**: Can produce CanonicalTimetable → same ValidationEngine
2. **Direct Editing**: UI changes → CanonicalTimetable → same ValidationEngine
3. **Bulk Publishing**: Validate multiple canonical timetables together
4. **Resource Validation**: Add resource rules to ValidationEngine
5. **Cross-Timetable Rules**: Detect conflicts across multiple teachers

## Conclusion

✅ ValidationEngine successfully implemented  
✅ XLSX pipeline rewired to use canonical validation  
✅ Legacy validator superseded  
✅ All 212 tests passing  
✅ No breaking changes to API contracts  
✅ ImportPreview is transport-only  
✅ Domain validation is authoritative  
✅ Ready for Phase 2 next steps  

**No issues or concerns blocking next implementation step.**
