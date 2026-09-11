# Domain Model — Canonical Timetable Representation

This package contains the **canonical domain model** for the Teacher Availability System.

## Purpose

The domain model provides a **source-agnostic** representation of timetable data that can be:

- **Produced by**: XLSX import, DOCX import (future), direct editing (future)
- **Consumed by**: Validation engine, persistence layer, preview generation
- **Independent of**: Transport formats, UI requirements, database schema

## Architecture

```
XLSX Import ─┐
DOCX Import ─┼──> CanonicalTimetable ──> Validation ──> Persistence ──> DRAFT/CONFIRMED
Editor UI ───┘
```

### Key Design Principles

1. **Source Location Traceability**: Every piece of data can reference its origin (XLSX sheet/row, DOCX table/row/col, manual edit)
2. **Structured Validation**: ValidationIssue provides ERROR/WARNING/INFO with affected entities and suggestions
3. **Teacher Identity Stability**: Teachers are stored once per timetable to avoid duplication/conflicts
4. **Logical Activities**: ScheduleActivity represents ONE logical entry that may span multiple time slots
5. **Immutable References**: SourceLocation is frozen to prevent accidental modification

## Core Types

### `SourceLocation`

Immutable reference to data origin.

**Supports**:
- XLSX: sheet_name, row_number, field_name
- DOCX: table_index, table_row, table_col
- MANUAL: display_context

**Example**:
```python
loc = SourceLocation(
    source_type="XLSX",
    sheet_name="Schedule",
    row_number=12,
    field_name="teacher_acronym"
)
print(loc.to_display())  # "XLSX: sheet 'Schedule', row 12, field 'teacher_acronym'"
```

### `ValidationIssue`

Structured validation feedback.

**Fields**:
- `severity`: ERROR (blocks confirmation) | WARNING | INFO
- `code`: Machine-readable identifier (e.g. "SLOT_OVERLAP")
- `message`: Human-readable explanation
- `source_locations`: List of SourceLocation references
- `affected_entities`: Dict of context (teacher, day, slot, etc.)
- `suggestion`: Optional resolution guidance

**Example**:
```python
issue = ValidationIssue(
    severity=ValidationSeverity.ERROR,
    code="SLOT_OVERLAP",
    message="Slot S4 on Monday overlaps with existing entry",
    source_locations=[source_loc],
    affected_entities={"teacher": "RAJ", "day": "monday", "slot": "S4"},
    suggestion="Remove the conflicting entry or choose a different slot"
)
```

### `TeacherIdentity`

Stable teacher reference within a timetable.

**Fields**:
- `acronym`: Stable identifier (e.g. "RAJ")
- `name`: Full name
- `level`: "UG" | "PG"
- `program_name`: Program (e.g. "MCA")
- `semester`: Semester number
- `department`: Department name
- `resolved_teacher_id`: UUID if existing DB teacher, None if new
- `action`: "CREATE" | "REUSE" | "CONFLICT"
- `issues`: List of ValidationIssue specific to this teacher

**Design Note**: Teachers are stored once per timetable to avoid having multiple conflicting copies of the same teacher's data across different activities.

### `ActivitySlotRange`

Day and slot occupancy for one activity.

**Fields**:
- `day_of_week`: ISO weekday (1=Monday, 7=Sunday)
- `slot_codes`: Ordered list of slot codes (e.g. ["S4", "S5"])
- `source_location`: Optional traceability

**Example**:
```python
# LAB spanning two slots
slot_range = ActivitySlotRange(
    day_of_week=4,  # Thursday
    slot_codes=["S4", "S5"],
    source_location=SourceLocation(source_type="XLSX", sheet_name="Schedule", row_number=15)
)
```

### `ScheduleActivity`

ONE logical schedule activity.

**Fields**:
- `teacher_acronym`: References TeacherIdentity.acronym
- `entry_type`: "CLASS" | "LAB" | "OTHER"
- `subject_or_activity`: Subject name or activity description
- `section`: Class section (optional)
- `room`: Room/lab identifier (optional)
- `notes`: Additional notes (optional)
- `slot_range`: When this activity occurs
- `issues`: Validation issues specific to this activity

**Design Note**: A LAB spanning S4+S5 is ONE ScheduleActivity with slot_range.slot_codes = ["S4", "S5"]. This preserves the "one logical activity" semantics.

### `CanonicalTimetable`

Complete timetable representation.

**Fields**:
- `import_id`: Unique identifier for this import/edit session
- `academic_year`: Target academic year (e.g. "2026-2027")
- `teachers`: List of TeacherIdentity
- `activities`: List of ScheduleActivity
- `global_issues`: Validation issues not tied to specific teacher/activity

**Methods**:
- `get_teacher(acronym: str)`: Retrieve teacher by acronym
- `has_errors()`: Check if any ERROR-level issues exist
- `get_activities_by_teacher(acronym: str)`: Get activities for a teacher

**Example**:
```python
timetable = CanonicalTimetable(
    import_id="abc-123",
    academic_year="2026-2027",
    teachers=[teacher1, teacher2],
    activities=[activity1, activity2, activity3],
    global_issues=[]
)

if timetable.has_errors():
    print("Cannot confirm: validation errors present")
```

## Converters

### `import_preview_to_canonical(preview: ImportPreview) -> CanonicalTimetable`

Converts transport model (ImportPreview) to canonical domain model.

**Preserves**:
- Source location information (sheet/row references)
- Warnings and errors as structured ValidationIssues
- Teacher resolution status (CREATE/REUSE/CONFLICT)

### `canonical_to_import_preview(canonical: CanonicalTimetable) -> ImportPreview`

Converts canonical domain model back to transport model for API responses.

**Use Case**: API endpoints return ImportPreview for compatibility, but staging stores CanonicalTimetable internally.

## Usage in XLSX Import Flow

```python
# 1. Parse XLSX (existing parser)
raw = parse_workbook(data, academic_year)

# 2. Normalize (existing normalizer)
preview = normalize(raw)

# 3. Validate (existing validator)
preview = resolve_and_validate(preview, db)

# 4. Convert to canonical (NEW)
canonical = import_preview_to_canonical(preview)

# 5. Stage canonical representation (NEW)
_STAGING[canonical.import_id] = canonical

# 6. Return preview for API response
return preview
```

On confirm:

```python
# 1. Retrieve canonical from staging
canonical = _STAGING.get(import_id)

# 2. Convert to preview for re-validation (preserves existing validator)
preview = canonical_to_import_preview(canonical)

# 3. Re-validate
preview = resolve_and_validate(preview, db)

# 4. Persist (existing persister)
result = persist_import(preview, db)
```

## Future Extensions

### DOCX Import (Phase 2 Next Steps)

```python
# Parse DOCX tables
docx_data = parse_docx_timetable(file_bytes)

# Convert directly to canonical
canonical = docx_to_canonical(docx_data, academic_year)

# Use same validation/persistence pipeline
canonical = validate_canonical(canonical, db)
persist_canonical(canonical, db)
```

### Direct Editing (Phase 3)

```python
# UI edit operation
edit_request = {
    "teacher_acronym": "RAJ",
    "day": "monday",
    "slot": "S4",
    "action": "add_class",
    "subject": "Data Structures"
}

# Convert edit to canonical activity
activity = edit_to_canonical_activity(edit_request)

# Validate
issues = validate_activity(activity, existing_timetable, db)

# If valid, apply
if not has_errors(issues):
    timetable.activities.append(activity)
    persist_canonical(timetable, db)
```

## Domain Rules Enforced

These institutional rules are enforced by the validation layer (not yet part of this foundation):

- **Working Slots**: S1–S9 (breaks 10:45–11:15, 13:05–14:00 are not working slots)
- **LAB Duration**: Exactly 2 consecutive working slots
- **CLASS/OTHER Duration**: 1 or 2 consecutive working slots
- **Max Duration**: 3+ consecutive slots invalid
- **Sunday**: Non-working day (day_of_week=7 invalid)
- **Slot Overlap**: Same teacher cannot occupy same slot twice on same day

## Testing

See `tests/test_canonical_domain.py` for comprehensive test coverage:

- SourceLocation construction and display (XLSX/DOCX/MANUAL)
- ValidationIssue with ERROR/WARNING/INFO levels
- TeacherIdentity with CREATE/REUSE/CONFLICT actions
- ScheduleActivity with single and multi-slot ranges
- CanonicalTimetable construction and utilities
- Bidirectional conversion (ImportPreview ↔ CanonicalTimetable)
- Source location preservation through conversions
- Error detection across teacher/activity/global levels

Run tests:
```bash
pytest tests/test_canonical_domain.py -v
```

## Files

- `timetable.py`: Core domain types (SourceLocation, ValidationIssue, CanonicalTimetable, etc.)
- `converters.py`: Bidirectional conversion between ImportPreview and CanonicalTimetable
- `__init__.py`: Public API exports
- `README.md`: This documentation

## Design Decisions

### Why store CanonicalTimetable in staging?

**Decision**: `_STAGING: dict[str, CanonicalTimetable]`

**Rationale**:
- Staging represents the "source of truth" for an in-progress import
- ImportPreview is a transport/UI format, not domain model
- Future DOCX/Editor sources will produce CanonicalTimetable directly
- Avoids needing multiple staging dictionaries for different sources

### Why not embed full TeacherIdentity in each ScheduleActivity?

**Decision**: Activities reference teachers by `acronym` string

**Rationale**:
- Avoids duplicate teacher data across activities
- Prevents conflicting teacher information (e.g., same acronym with different names)
- Centralizes teacher data in `timetable.teachers[]`
- Mirrors the database schema (teachers table + foreign keys)

### Why frozen SourceLocation?

**Decision**: `@dataclass(frozen=True)` on SourceLocation

**Rationale**:
- Source references should never change after creation
- Immutability prevents accidental modification
- Enables use as dict keys if needed
- Signals semantic intent: "this is a reference, not mutable state"

### Why not use Pydantic?

**Decision**: Python dataclasses for domain model

**Rationale**:
- Dataclasses are simpler and sufficient for domain objects
- No serialization/validation framework needed at domain layer
- Pydantic remains for API boundaries (ImportPreview, etc.)
- Keeps domain layer lightweight and framework-agnostic

## Migration Notes

### Existing Code Impact

**Breaking Changes**: None

**Modified Files**:
- `backend/app/api/imports.py`: Now stages CanonicalTimetable instead of ImportPreview
  - API contracts unchanged (still returns ImportPreview)
  - Staging is internal implementation detail

**Unchanged**:
- Parser: `parse_workbook()` unchanged
- Normalizer: `normalize()` unchanged
- Validator: `resolve_and_validate()` unchanged
- Persister: `persist_import()` unchanged
- Database schema: No changes
- API contracts: No changes
- Frontend: No changes required

### Backward Compatibility

All existing XLSX import tests pass without modification. The canonical model acts as an **internal layer** that does not affect external contracts.
