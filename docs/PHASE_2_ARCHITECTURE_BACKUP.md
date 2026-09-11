# PHASE 2 ARCHITECTURE — Teacher Availability System

**Document Version**: 1.0  
**Date**: 2026-09-10  
**Git Baseline**: commit `6ef827f` — "Implement timetable import and availability workflow"

---

## EXECUTIVE SUMMARY

This document proposes the Phase 2 architecture for the Teacher Availability System based on teacher feedback from the working prototype. The current Excel-based import will be extended to support DOCX files, a comprehensive validation/inconsistency engine will be built, teacher identity will become department-aware, resource availability will be added alongside teacher availability, and direct editing with bulk review/publish will replace the per-teacher confirmation workflow.

**Key Architectural Decisions:**

1. **Canonical Model**: Both DOCX and XLSX importers will produce a unified `ImportPreview` schema
2. **Resource Strategy**: Introduce normalized `Resource` entity (rooms/labs) with department context
3. **Teacher Identity**: Migrate from globally unique acronym to department-scoped identity
4. **Validation Engine**: Structured error/warning/info system with actionable severity levels
5. **Bulk Operations**: Department-wide import → review → fix → publish workflow
6. **Direct Editing**: Rich timetable editor with same validation as imports

**Team Split:**
- **Koushik**: DOCX Import → Validation Engine → Teacher Identity
- **Lipika**: UI Polish → Resource Availability → Direct Editing + Bulk Publish

---


## 1. CURRENT ARCHITECTURE ASSESSMENT

### 1.1 Existing Strengths

**Database Design**:
- Clean normalized schema with proper foreign keys
- Logical separation: `schedule_entries` (activities) + `schedule_entry_slots` (occupancy)
- DRAFT/CONFIRMED workflow prevents accidental overwrites
- Deferred constraint triggers enforce slot uniqueness at CONFIRMED promotion
- CASCADE deletes maintain referential integrity
- UUID primary keys enable distributed systems

**Import Pipeline**:
- Three-stage architecture: `parser.py` → `normalizer.py` → `validator.py` → `persister.py`
- Clean separation of concerns (structure vs semantics vs DB resolution)
- Staging area (`_STAGING` dict) allows preview before commit
- Bulk insert optimization for `ScheduleEntrySlot` (recent fix)
- Transactional safety: all-or-nothing imports

**API Design**:
- RESTful with explicit versioning (`/api/v1/`)
- Exact-match semantics: no ambiguous fallbacks
- Clear 404 vs 422 vs 409 error contracts
- Pydantic validation at API boundary
- Query parameters for read filtering

**Frontend**:
- TypeScript provides type safety
- TanStack Query handles server state elegantly
- Slot-level + entry-level data model correctly maintained
- Daily and weekly views working
- Multi-slot activities (LABs) render correctly with visual merging

### 1.2 Existing Weaknesses

**Import**:
- XLSX-only (teachers need DOCX support for existing workflows)
- Two-sheet model (Teachers + Schedule) doesn't match DOCX structure
- No traceability: errors don't point to exact source cell/table/row
- Limited validation scope (no room conflicts, teacher overlaps, resource booking)

**Teacher Identity**:
- Globally unique acronym assumption breaks at department scale
- `uq_teachers_acronym_normalized` constraint will cause collisions
- Search doesn't filter by department context

**Resource Management**:
- `room` is free text in `schedule_entries.room`
- No normalization → "Lab1A" vs "Lab 1A" treated as different rooms
- No resource availability query
- No resource overlap detection

**UI**:
- Director view shows "FREE" in every empty slot (requirement: style-based availability only)
- No direct editing: users can't add/move/delete activities in UI
- Per-teacher confirmation doesn't scale to 100+ teachers
- No bulk review/publish workflow

**Validation**:
- Basic slot validation (LAB pairs, duplicate slots per day)
- No teacher overlap detection
- No resource conflict detection
- No cross-activity validation
- Warnings vs errors not clearly separated

---


## 2. CANONICAL MODEL PROPOSAL

### 2.1 Philosophy

Both DOCX and XLSX importers must produce the **same internal representation** before validation. This ensures:
- Validation logic is written once
- Frontend preview UI is format-agnostic
- Persistence layer doesn't know/care about source format

### 2.2 Enhanced Import Preview Schema

```python
# backend/app/schemas/imports.py (extended)

class SourceLocation(BaseModel):
    """Traceability: where did this data come from?"""
    file_type: Literal["XLSX", "DOCX"]
    # XLSX: sheet_name + row_number
    sheet_name: str | None = None
    row_number: int | None = None
    # DOCX: table_index + row_index (within that table)
    table_index: int | None = None
    table_row_index: int | None = None
    # Human-readable reference for error messages
    display_ref: str  # e.g. "Teachers!5" or "Table 2, Row 7"


class TeacherImportRow(BaseModel):
    row_ref: str  # DEPRECATED in favor of source_location
    source_location: SourceLocation  # NEW
    action: Literal["CREATE", "REUSE", "CONFLICT"]
    name: str
    acronym: str
    level: str
    program_name: str
    semester: int
    department: str
    resolved_teacher_id: UUID | None = None
    warnings: list[str] = []
    errors: list[str] = []  # NEW: per-teacher errors


class ScheduleImportRow(BaseModel):
    row_refs: list[str]  # DEPRECATED
    source_locations: list[SourceLocation]  # NEW: LABs may span multiple source rows
    teacher_acronym: str
    teacher_department: str | None = None  # NEW: for department-scoped resolution
    day: str
    slot_ids: list[str]
    entry_type: str
    subject_or_activity: str | None = None
    section: str | None = None
    room: str | None = None  # Will resolve to Resource entity
    notes: str | None = None
    warnings: list[str] = []
    errors: list[str] = []  # NEW


class ValidationIssue(BaseModel):
    """Structured validation result"""
    severity: Literal["ERROR", "WARNING", "INFO"]
    code: str  # e.g. "TEACHER_OVERLAP", "RESOURCE_CONFLICT"
    message: str  # Human-readable
    source_locations: list[SourceLocation] = []
    affected_entities: dict[str, Any] = {}  # e.g. {"teacher": "DNS", "day": "thursday", "slots": ["S2", "S3"]}
    suggestion: str | None = None  # Recovery hint


class ImportPreview(BaseModel):
    import_id: str
    academic_year: str
    department: str | None = None  # NEW: imports may be department-scoped
    teachers: list[TeacherImportRow] = []
    days: dict[str, list[ScheduleImportRow]] = {}
    validation_issues: list[ValidationIssue] = []  # NEW: replaces flat warnings/errors
    # DEPRECATED: warnings/errors lists (kept for backward compat during migration)
    warnings: list[str] = []
    errors: list[str] = []
```

### 2.3 Parser Output Contract

**XLSX Parser** (`backend/app/services/excel_import/parser.py`):
- Reads Teachers + Schedule sheets
- Produces `RawWorkbook` with `SourceLocation` set to `file_type="XLSX"`, `sheet_name`, `row_number`

**DOCX Parser** (NEW: `backend/app/services/docx_import/parser.py`):
- Reads WordprocessingML tables
- Reconstructs geometry (merged cells, gridSpan, vMerge)
- Produces `RawWorkbook` with `SourceLocation` set to `file_type="DOCX"`, `table_index`, `table_row_index`

Both parsers emit:
```python
class RawWorkbook(BaseModel):
    academic_year: str
    teacher_rows: list[RawTeacherRow]
    schedule_rows: list[RawScheduleRow]
```

Where each raw row carries its `SourceLocation`.

---


## 3. DOCX IMPORTER ARCHITECTURE

### 3.1 Problem Statement

DOCX timetables contain:
- **Merged cells** (teacher spans multiple rows, activity spans slots)
- **gridSpan** (horizontal cell merging in WordprocessingML)
- **vMerge** (vertical cell merging)
- **Multiple sections/batches** in one table
- **Multiple teachers** in one table
- **Complex geometry** requiring cell coordinate reconstruction

**DO NOT** parse DOCX as text extraction + regex. This will fail on merged cells and ambiguous layouts.

### 3.2 Strategy: Structure-Aware Table Parser

Use `python-docx` library to access WordprocessingML table model:

```python
from docx import Document
from docx.table import Table, _Cell
from docx.oxml.table import CT_Tbl, CT_Tc

# Access table.rows, table.columns, cell.text
# Detect vMerge via cell._tc.tcPr.vMerge
# Detect gridSpan via cell._tc.tcPr.gridSpan
```

### 3.3 DOCX Parser Architecture

```
backend/app/services/docx_import/
├── __init__.py
├── parser.py          # Entry point: extract tables → RawWorkbook
├── table_geometry.py  # Reconstruct cell grid from merged cells
├── row_classifier.py  # Identify header vs data rows
├── field_extractor.py # Extract teacher/day/time/activity from cells
└── normalizer.py      # Convert DOCX rows → canonical ScheduleImportRow
```

#### 3.3.1 Parsing Phases

**Phase 1: Table Extraction**
```python
def parse_docx(file_bytes: bytes) -> list[Table]:
    """Extract all tables from DOCX"""
    doc = Document(BytesIO(file_bytes))
    return doc.tables
```

**Phase 2: Geometry Reconstruction**
```python
class CellCoord(BaseModel):
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    text: str
    
def reconstruct_grid(table: Table) -> list[list[CellCoord]]:
    """
    Build a 2D grid where merged cells appear in all their occupied positions.
    Returns: grid[row][col] = CellCoord (with span info)
    """
    # Algorithm:
    # 1. Iterate table.rows
    # 2. For each cell, detect gridSpan (horizontal merge)
    # 3. Detect vMerge (vertical merge): "restart" vs "continue"
    # 4. Fill grid positions accordingly
```

**Phase 3: Row Classification**
```python
def classify_row(row_cells: list[CellCoord]) -> Literal["HEADER", "TEACHER", "SCHEDULE", "EMPTY"]:
    """
    HEADER: contains "Day", "Time", "Faculty"
    TEACHER: teacher name/acronym/department
    SCHEDULE: day + time + activity data
    EMPTY: all cells blank
    """
```

**Phase 4: Field Extraction**
```python
def extract_schedule_row(
    row: list[CellCoord],
    header_map: dict[int, str],  # col_index -> field_name
    context: dict  # accumulated context (current teacher, section, etc.)
) -> RawScheduleRow | None:
    """
    Extract: teacher, day, time, subject, section, room
    Handle:
    - Teacher name appears once, spans multiple schedule rows
    - Section appears in merged cell
    - Multi-slot activities span consecutive time rows
    """
```

**Phase 5: Ambiguity Detection**
```python
class AmbiguousMapping(Exception):
    """Raised when cell geometry prevents clear field assignment"""
    pass

# Example ambiguous cases:
# - No clear day column
# - Time string doesn't match institutional slots
# - Multiple possible teacher assignments
# - Merged cell spans incompatible fields
```

When ambiguity is detected:
- Raise `ParseError` with specific `SourceLocation`
- Return `ValidationIssue` with `severity="ERROR"`, `code="AMBIGUOUS_LAYOUT"`
- DO NOT GUESS

### 3.4 DOCX-to-Canonical Mapping

```python
# backend/app/services/docx_import/normalizer.py

def normalize_docx_rows(
    raw_rows: list[RawScheduleRow],
    academic_year: str
) -> ImportPreview:
    """
    Convert DOCX-specific raw rows into canonical ScheduleImportRow.
    Identical output schema to XLSX normalizer.
    """
    # Same logic as excel_import/normalizer.py but source_location differs
```

### 3.5 Testing Strategy

**Unit Tests** (DOCX parser):
```python
# tests/test_docx_import.py
def test_simple_table_no_merges():
    """Basic table: 1 teacher, 1 day, 1 activity"""

def test_vertical_merge_teacher_name():
    """Teacher name spans 5 schedule rows (vMerge)"""

def test_horizontal_merge_section():
    """Section cell spans 3 columns (gridSpan)"""

def test_lab_spanning_two_time_rows():
    """LAB activity occupies 2 consecutive time slots"""

def test_multiple_teachers_same_table():
    """Table contains 3 teachers, each with schedule"""

def test_ambiguous_day_column_rejected():
    """No clear day column → ParseError"""

def test_invalid_time_string_rejected():
    """Time '3:00 PM - 4:30 PM' not matching S1-S9 → error"""
```

---


## 4. VALIDATION / INCONSISTENCY ENGINE

### 4.1 Goals

Detect incorrect timetable data with actionable error messages. Support three severity levels:
- **ERROR**: Blocks import/publish
- **WARNING**: User should review but can proceed
- **INFO**: Informational notice

### 4.2 Validation Rules

#### 4.2.1 Teacher Validation
| Rule | Code | Severity | Blocks Import? |
|------|------|----------|----------------|
| Teacher overlap (same slot, different activities) | `TEACHER_OVERLAP` | ERROR | Yes |
| Missing teacher name | `TEACHER_MISSING_NAME` | ERROR | Yes |
| Missing teacher acronym | `TEACHER_MISSING_ACRONYM` | ERROR | Yes |
| Duplicate teacher acronym (same department) | `TEACHER_DUPLICATE_ACRONYM` | ERROR | Yes |
| Duplicate teacher acronym (different department) | `TEACHER_ACRONYM_CROSS_DEPT` | INFO | No |
| Teacher not found in DB | `TEACHER_NOT_FOUND` | ERROR | Yes |
| Teacher inactive | `TEACHER_INACTIVE` | WARNING | No |
| Teacher has no assignments | `TEACHER_NO_ASSIGNMENTS` | INFO | No |

#### 4.2.2 Resource Validation
| Rule | Code | Severity | Blocks Import? |
|------|------|----------|----------------|
| Resource overlap (same slot, different activities) | `RESOURCE_OVERLAP` | ERROR | Yes |
| Unknown room | `RESOURCE_UNKNOWN` | WARNING | No |
| Resource inactive | `RESOURCE_INACTIVE` | WARNING | No |
| Room name ambiguous (alias mismatch) | `RESOURCE_AMBIGUOUS_ALIAS` | WARNING | No |

#### 4.2.3 Schedule Validation
| Rule | Code | Severity | Blocks Import? |
|------|------|----------|----------------|
| Invalid day | `SCHEDULE_INVALID_DAY` | ERROR | Yes |
| Sunday schedule | `SCHEDULE_SUNDAY` | ERROR | Yes |
| Invalid time slot | `SCHEDULE_INVALID_SLOT` | ERROR | Yes |
| Break conflict | `SCHEDULE_BREAK_CONFLICT` | ERROR | Yes |
| Lunch conflict | `SCHEDULE_LUNCH_CONFLICT` | ERROR | Yes |
| LAB not exactly 2 consecutive slots | `SCHEDULE_LAB_INVALID_DURATION` | ERROR | Yes |
| LAB slots non-consecutive | `SCHEDULE_LAB_NON_CONSECUTIVE` | ERROR | Yes |
| LAB crosses break | `SCHEDULE_LAB_BREAK_CROSSING` | ERROR | Yes |
| Duplicate activity (same teacher/day/slot/subject/section) | `SCHEDULE_DUPLICATE_ACTIVITY` | WARNING | No |
| Slot already occupied (within same day/teacher) | `SCHEDULE_SLOT_COLLISION` | ERROR | Yes |

#### 4.2.4 File Validation
| Rule | Code | Severity | Blocks Import? |
|------|------|----------|----------------|
| Wrong file extension | `FILE_WRONG_EXTENSION` | ERROR | Yes |
| Corrupt file | `FILE_CORRUPT` | ERROR | Yes |
| Empty file | `FILE_EMPTY` | ERROR | Yes |
| Malformed DOCX | `FILE_MALFORMED_DOCX` | ERROR | Yes |
| Malformed XLSX | `FILE_MALFORMED_XLSX` | ERROR | Yes |
| Missing required sheet | `FILE_MISSING_SHEET` | ERROR | Yes |
| Missing required table | `FILE_MISSING_TABLE` | ERROR | Yes |

### 4.3 Validation Engine Architecture

```python
# backend/app/services/validation/
├── __init__.py
├── engine.py              # Orchestrator: runs all validators
├── teacher_validator.py   # Teacher-specific rules
├── resource_validator.py  # Resource conflict detection
├── schedule_validator.py  # Schedule rule validation
├── cross_validator.py     # Cross-entity validation (overlaps)
└── issue_formatter.py     # Convert validation results → ValidationIssue schema
```

#### 4.3.1 Validation Engine Interface

```python
# backend/app/services/validation/engine.py

class ValidationEngine:
    def __init__(self, db: Session):
        self.db = db
        self.teachers_validator = TeacherValidator(db)
        self.resource_validator = ResourceValidator(db)
        self.schedule_validator = ScheduleValidator(db)
        self.cross_validator = CrossValidator(db)
    
    def validate(self, preview: ImportPreview) -> list[ValidationIssue]:
        """
        Run all validation rules.
        Returns: list of ValidationIssue (ERROR/WARNING/INFO)
        """
        issues: list[ValidationIssue] = []
        
        # Phase 1: Per-entity validation
        issues.extend(self.teachers_validator.validate(preview.teachers))
        issues.extend(self.schedule_validator.validate_structure(preview.days))
        
        # Phase 2: DB resolution validation
        issues.extend(self.teachers_validator.validate_db_conflicts(preview.teachers))
        issues.extend(self.resource_validator.validate_rooms(preview.days))
        
        # Phase 3: Cross-entity validation
        issues.extend(self.cross_validator.detect_teacher_overlaps(preview.days))
        issues.extend(self.cross_validator.detect_resource_overlaps(preview.days))
        
        return issues
```

#### 4.3.2 Teacher Overlap Detection

```python
# backend/app/services/validation/cross_validator.py

def detect_teacher_overlaps(
    self, days: dict[str, list[ScheduleImportRow]]
) -> list[ValidationIssue]:
    """
    For each teacher:
      For each day:
        Build slot → activity map
        If one slot has >1 activity → TEACHER_OVERLAP error
    """
    issues = []
    by_teacher: dict[str, dict[str, list[ScheduleImportRow]]] = {}
    
    for day_name, rows in days.items():
        for row in rows:
            by_teacher.setdefault(row.teacher_acronym, {}).setdefault(day_name, []).append(row)
    
    for teacher, days_map in by_teacher.items():
        for day_name, activities in days_map.items():
            slot_usage: dict[str, list[ScheduleImportRow]] = {}
            for activity in activities:
                for slot in activity.slot_ids:
                    slot_usage.setdefault(slot, []).append(activity)
            
            for slot, conflicting in slot_usage.items():
                if len(conflicting) > 1:
                    issues.append(ValidationIssue(
                        severity="ERROR",
                        code="TEACHER_OVERLAP",
                        message=f"Teacher {teacher} has overlapping activities on {day_name} at {slot}",
                        source_locations=[loc for activity in conflicting for loc in activity.source_locations],
                        affected_entities={
                            "teacher": teacher,
                            "day": day_name,
                            "slot": slot,
                            "activities": [a.subject_or_activity for a in conflicting]
                        },
                        suggestion="Remove or reschedule one of the conflicting activities"
                    ))
    
    return issues
```

#### 4.3.3 Resource Overlap Detection

```python
def detect_resource_overlaps(
    self, days: dict[str, list[ScheduleImportRow]]
) -> list[ValidationIssue]:
    """
    For each resource (room):
      For each day:
        For each slot:
          If >1 activity uses this resource → RESOURCE_OVERLAP error
    """
    # Similar to teacher overlap but keyed by (resource, day, slot)
```

### 4.4 Integration with Existing Flow

Current flow:
```
POST /imports/excel
  → parse_workbook
  → normalize
  → resolve_and_validate (simple validation)
  → return ImportPreview
```

Enhanced flow:
```
POST /imports/excel (or /imports/docx)
  → parse_workbook (XLSX or DOCX parser)
  → normalize (format-agnostic)
  → ValidationEngine.validate(preview)
  → populate preview.validation_issues
  → return ImportPreview
```

Confirm endpoint checks:
```python
# backend/app/api/imports.py
@router.post("/{import_id}/confirm")
def confirm_import(import_id: str, db: Session = Depends(get_db)):
    preview = _STAGING[import_id]
    
    # Re-validate
    engine = ValidationEngine(db)
    issues = engine.validate(preview)
    errors = [i for i in issues if i.severity == "ERROR"]
    
    if errors:
        raise HTTPException(422, detail={
            "message": "Validation errors prevent confirmation",
            "issues": [i.dict() for i in errors]
        })
    
    # Proceed with persist_import()
    ...
```

---


## 5. RESOURCE STRATEGY

### 5.1 Decision: Introduce Resource Entity

**Recommendation**: Migrate from free-text `schedule_entries.room` to normalized `Resource` entity.

**Rationale**:
- **Conflict detection**: Cannot detect "Lab1A" vs "Lab 1A" as same resource with string comparison
- **Queryability**: "Show me all activities in Lab1A" requires exact string match
- **Department filtering**: Resources belong to departments (CA labs vs other departments)
- **Alias handling**: Multiple names for same physical space (FDC vs Seminar Hall)
- **Future scaling**: Resource types (LAB, CLASSROOM, SEMINAR_HALL), capacity, equipment

### 5.2 Resource Schema

```sql
-- database/schema.sql (additions)

CREATE TABLE IF NOT EXISTS resources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,                     -- Display name: "Lab 1A"
    normalized_name TEXT NOT NULL,          -- Searchable: "lab1a"
    resource_type TEXT NOT NULL             -- LAB, CLASSROOM, SEMINAR_HALL, etc.
        CHECK (resource_type IN ('LAB', 'CLASSROOM', 'SEMINAR_HALL', 'AUDITORIUM', 'OTHER')),
    department TEXT NOT NULL DEFAULT 'Computer Applications',
    capacity SMALLINT,                      -- Optional: max students
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT resources_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT resources_normalized_name_not_blank CHECK (btrim(normalized_name) <> '')
);

-- Normalized name unique within department
CREATE UNIQUE INDEX IF NOT EXISTS uq_resources_normalized_name_dept
    ON resources (lower(btrim(normalized_name)), lower(btrim(department)));

-- Efficient name search
CREATE INDEX IF NOT EXISTS idx_resources_normalized_name
    ON resources (lower(btrim(normalized_name)));

-- Optional: Resource aliases (for "Lab1A" = "Lab 1A" = "CA Lab 1A")
CREATE TABLE IF NOT EXISTS resource_aliases (
    resource_id UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    PRIMARY KEY (resource_id, alias)
);

CREATE INDEX IF NOT EXISTS idx_resource_aliases_alias
    ON resource_aliases (lower(btrim(alias)));
```

### 5.3 Schedule Entry Foreign Key

```sql
-- Migration: add resource_id FK to schedule_entries
ALTER TABLE schedule_entries
    ADD COLUMN resource_id UUID REFERENCES resources(id);

-- Keep room TEXT for backward compat during migration
-- After migration: deprecate room column, make resource_id NOT NULL
```

### 5.4 Resource Resolution During Import

```python
# backend/app/services/validation/resource_validator.py

class ResourceValidator:
    def __init__(self, db: Session):
        self.db = db
        self._resource_cache: dict[str, UUID] = {}  # normalized_name -> id
    
    def resolve_room_name(
        self, room_text: str, department: str
    ) -> tuple[UUID | None, list[ValidationIssue]]:
        """
        Try to resolve free-text room name to Resource ID.
        Returns: (resource_id, issues)
        
        Cases:
        1. Exact match on name → return resource_id
        2. Exact match on alias → return resource_id
        3. Multiple matches → WARNING (ambiguous)
        4. No match → WARNING (unknown resource, will create placeholder)
        """
        normalized = normalize_room_name(room_text)  # "lab1a"
        
        # Check cache
        cache_key = f"{department.lower()}:{normalized}"
        if cache_key in self._resource_cache:
            return self._resource_cache[cache_key], []
        
        # Query DB
        resource = self.db.scalar(
            select(Resource).where(
                Resource.department.ilike(department),
                func.lower(func.btrim(Resource.normalized_name)) == normalized,
                Resource.is_active.is_(True)
            )
        )
        
        if resource:
            self._resource_cache[cache_key] = resource.id
            return resource.id, []
        
        # Check aliases
        alias_match = self.db.scalar(
            select(Resource)
            .join(ResourceAlias)
            .where(
                func.lower(func.btrim(ResourceAlias.alias)) == normalized,
                Resource.department.ilike(department),
                Resource.is_active.is_(True)
            )
        )
        
        if alias_match:
            self._resource_cache[cache_key] = alias_match.id
            return alias_match.id, []
        
        # Not found → return WARNING
        return None, [
            ValidationIssue(
                severity="WARNING",
                code="RESOURCE_UNKNOWN",
                message=f"Resource '{room_text}' not found in database",
                affected_entities={"room_text": room_text, "department": department},
                suggestion="Resource will be auto-created during import if confirmed"
            )
        ]
```

### 5.5 Resource Auto-Creation

During `persist_import`, if a room name doesn't resolve:
```python
# backend/app/services/excel_import/persister.py

def get_or_create_resource(
    db: Session, room_text: str, department: str
) -> UUID:
    """
    Find existing resource or create placeholder.
    """
    normalized = normalize_room_name(room_text)
    
    resource = db.scalar(
        select(Resource).where(
            Resource.department == department,
            func.lower(Resource.normalized_name) == normalized
        )
    )
    
    if resource:
        return resource.id
    
    # Create placeholder
    new_resource = Resource(
        id=uuid.uuid4(),
        name=room_text.strip(),
        normalized_name=normalized,
        resource_type="OTHER",  # Default type
        department=department,
        is_active=True
    )
    db.add(new_resource)
    db.flush()
    return new_resource.id
```

### 5.6 Migration Strategy

**Phase 1**: Add `resource_id` column (nullable)
**Phase 2**: Backfill existing `schedule_entries.room` → resolve or create `Resource` records
**Phase 3**: Make `resource_id NOT NULL`, deprecate `room` column
**Phase 4**: Drop `room` column (breaking change, coordinate with frontend)

---


## 6. TEACHER IDENTITY STRATEGY

### 6.1 Problem

Current: globally unique `uq_teachers_acronym_normalized` constraint.

**Collision scenario**:
- Computer Applications dept: "DNS" → Faculty A
- Other Department: "DNS" → Faculty B
- Current system: second insert fails with 409 CONFLICT

### 6.2 Solution: Department-Scoped Identity

Migrate to: **department + normalized acronym** uniqueness.

### 6.3 Database Migration

```sql
-- Step 1: Drop existing unique index
DROP INDEX IF EXISTS uq_teachers_acronym_normalized;

-- Step 2: Create composite unique index
CREATE UNIQUE INDEX uq_teachers_acronym_dept_normalized
    ON teachers (lower(btrim(acronym)), lower(btrim(department)));

-- Step 3: Add index for cross-department acronym search
CREATE INDEX idx_teachers_acronym_all_depts
    ON teachers (lower(btrim(acronym)));
```

### 6.4 API Changes

#### 6.4.1 Teacher Search (Modified)

```python
# backend/app/api/teachers.py

@router.get("/search", response_model=list[TeacherOut])
def search_teachers(
    q: str = Query(min_length=1),
    department: str | None = Query(default=None),  # NEW: optional filter
    db: Session = Depends(get_db),
) -> list[Teacher]:
    """
    Search teachers by name or acronym.
    If department provided: search within that department only.
    If department omitted: search across all departments.
    """
    term = q.strip()
    pattern = f"%{term}%"
    
    stmt = (
        select(Teacher)
        .options(selectinload(Teacher.program))
        .where(
            Teacher.is_active.is_(True),
            (Teacher.name.ilike(pattern) | Teacher.acronym.ilike(pattern)),
        )
    )
    
    if department:
        stmt = stmt.where(Teacher.department.ilike(department.strip()))
    
    stmt = stmt.order_by(Teacher.department, Teacher.name).limit(25)
    return list(db.scalars(stmt).all())
```

#### 6.4.2 Teacher Resolution in Import (Modified)

```python
# backend/app/services/excel_import/validator.py

def resolve_teacher(
    acronym: str, 
    department: str,  # NEW: required parameter
    db: Session
) -> Teacher | None:
    """
    Resolve teacher by (acronym, department).
    Returns None if not found.
    """
    return db.scalar(
        select(Teacher).where(
            func.lower(func.btrim(Teacher.acronym)) == acronym.lower().strip(),
            func.lower(func.btrim(Teacher.department)) == department.lower().strip(),
            Teacher.is_active.is_(True)
        )
    )
```

### 6.5 Import Schema Changes

```python
# backend/app/schemas/imports.py

class TeacherImportRow(BaseModel):
    # ... existing fields ...
    department: str  # ALREADY EXISTS, now used for resolution

class ScheduleImportRow(BaseModel):
    teacher_acronym: str
    teacher_department: str | None = None  # NEW: for cross-referencing
    # ... rest unchanged ...
```

**DOCX Import**: Extract department from teacher metadata section in table.
**XLSX Import**: Read from Teachers sheet `department` column (already exists).

### 6.6 Frontend Changes

#### 6.6.1 Director Search UI

```tsx
// frontend/src/pages/DirectorPage.tsx

function DirectorSearch() {
  const [query, setQuery] = useState('');
  const [department, setDepartment] = useState<string>('');  // NEW
  
  const { data: teachers } = useQuery({
    queryKey: ['teachers', 'search', query, department],
    queryFn: () => searchTeachers(query, department),  // Pass department param
  });
  
  return (
    <>
      <input 
        placeholder="Search by name or acronym"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <select 
        value={department}
        onChange={(e) => setDepartment(e.target.value)}
      >
        <option value="">All Departments</option>
        <option value="Computer Applications">Computer Applications</option>
        <option value="Commerce">Commerce</option>
        {/* Dynamic department list from API */}
      </select>
    </>
  );
}
```

#### 6.6.2 API Client

```typescript
// frontend/src/services/api.ts

export async function searchTeachers(
  query: string,
  department?: string  // NEW: optional parameter
): Promise<Teacher[]> {
  const params: any = { q: query };
  if (department) params.department = department;
  
  const response = await api.get<Teacher[]>('/teachers/search', { params });
  return response.data;
}
```

### 6.7 Migration Risks

**Risk 1**: Existing data may have duplicate acronyms across departments (currently blocked by constraint).
- **Mitigation**: Before dropping old constraint, run audit query:
  ```sql
  SELECT acronym, COUNT(DISTINCT department) as dept_count
  FROM teachers
  GROUP BY lower(btrim(acronym))
  HAVING COUNT(DISTINCT department) > 1;
  ```
  If any results: this is GOOD (means departments already differ), constraint change is safe.

**Risk 2**: Frontend code assumes globally unique acronyms.
- **Mitigation**: Audit frontend for `acronym` usage, ensure `teacher.id` (UUID) is primary key for lookups.

---


## 7. RESOURCE AVAILABILITY ARCHITECTURE

### 7.1 Requirements

- Director should search for resources (Lab1A, CA1, FDC, etc.)
- Show resource availability similar to teacher availability
- Same schedule activities drive both teacher and resource views
- Daily and weekly views

### 7.2 Resource API Endpoints

```python
# backend/app/api/resources.py (NEW)

router = APIRouter(prefix="/resources", tags=["resources"])

@router.get("", response_model=list[ResourceOut])
def list_resources(
    department: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    db: Session = Depends(get_db)
) -> list[Resource]:
    """List active resources, optionally filtered by department/type"""
    stmt = select(Resource).where(Resource.is_active.is_(True))
    if department:
        stmt = stmt.where(Resource.department.ilike(department.strip()))
    if resource_type:
        stmt = stmt.where(Resource.resource_type == resource_type.upper())
    stmt = stmt.order_by(Resource.department, Resource.name)
    return list(db.scalars(stmt).all())


@router.get("/search", response_model=list[ResourceOut])
def search_resources(
    q: str = Query(min_length=1),
    department: str | None = Query(default=None),
    db: Session = Depends(get_db)
) -> list[Resource]:
    """Search resources by name (including aliases)"""
    term = q.strip()
    pattern = f"%{term}%"
    
    # Search resources + aliases
    stmt = (
        select(Resource)
        .where(
            Resource.is_active.is_(True),
            Resource.name.ilike(pattern) | Resource.normalized_name.ilike(pattern)
        )
    )
    if department:
        stmt = stmt.where(Resource.department.ilike(department.strip()))
    stmt = stmt.order_by(Resource.name).limit(25)
    
    return list(db.scalars(stmt).all())


@router.get("/{resource_id}/timetable", response_model=ResourceDayOut)
def get_resource_timetable_day(
    resource_id: UUID,
    day: str = Query(description="Lowercase day name"),
    academic_year: str = Query(description="Academic year e.g. 2025-2026"),
    db: Session = Depends(get_db)
) -> ResourceDayOut:
    """
    Return resource occupancy for a specific day.
    
    Logic:
    1. Find all CONFIRMED timetables for academic_year
    2. Find all schedule_entries where resource_id = {resource_id} AND day_of_week = {day}
    3. Build periods grid showing occupied vs free slots
    """
    day_iso = DAY_NAME_TO_ISO.get(day.strip().lower())
    if not day_iso:
        raise HTTPException(400, f"Invalid day: {day}")
    
    # Verify resource exists
    resource = db.scalar(select(Resource).where(Resource.id == resource_id))
    if not resource:
        raise HTTPException(404, "Resource not found")
    
    # Find all activities using this resource on this day (CONFIRMED only)
    entries = db.scalars(
        select(ScheduleEntry)
        .join(Timetable)
        .options(
            selectinload(ScheduleEntry.timetable).selectinload(Timetable.teacher),
            selectinload(ScheduleEntry.slot_links).selectinload(ScheduleEntrySlot.time_slot)
        )
        .where(
            Timetable.academic_year == academic_year.strip(),
            Timetable.status == "CONFIRMED",
            ScheduleEntry.resource_id == resource_id,
            ScheduleEntry.day_of_week == day_iso
        )
    ).all()
    
    # Build slot_code -> activity map
    uuid_to_code = _get_slot_uuid_to_code(db)
    slot_to_activity: dict[str, ResourceActivityOut] = {}
    
    for entry in entries:
        codes = [uuid_to_code[link.time_slot_id] for link in entry.slot_links if link.time_slot_id in uuid_to_code]
        activity_out = ResourceActivityOut(
            id=entry.id,
            teacher_id=entry.timetable.teacher_id,
            teacher_name=entry.timetable.teacher.name,
            teacher_acronym=entry.timetable.teacher.acronym,
            entry_type=entry.entry_type,  # type: ignore
            subject_or_activity=entry.subject_or_activity,
            section=entry.section,
            slot_codes=sorted(codes)
        )
        for code in codes:
            slot_to_activity[code] = activity_out
    
    # Build periods response
    periods: list[ResourcePeriodOut] = []
    for period_cfg in PERIOD_SEQUENCE:
        if period_cfg.kind == "BREAK":
            periods.append(ResourcePeriodOut(
                kind="BREAK",
                label=period_cfg.label,
                start_time=period_cfg.start_time,
                end_time=period_cfg.end_time,
                activity=None
            ))
        else:
            periods.append(ResourcePeriodOut(
                kind="SLOT",
                code=period_cfg.code,
                start_time=period_cfg.start_time,
                end_time=period_cfg.end_time,
                activity=slot_to_activity.get(period_cfg.code)
            ))
    
    return ResourceDayOut(
        resource_id=resource_id,
        resource_name=resource.name,
        academic_year=academic_year,
        day=day.strip().lower(),
        periods=periods
    )
```

### 7.3 Resource Response Schemas

```python
# backend/app/schemas/resource.py (NEW)

class ResourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    name: str
    normalized_name: str
    resource_type: str
    department: str
    capacity: int | None = None
    is_active: bool


class ResourceActivityOut(BaseModel):
    """An activity occupying a resource slot"""
    id: UUID
    teacher_id: UUID
    teacher_name: str
    teacher_acronym: str
    entry_type: str
    subject_or_activity: str
    section: str | None
    slot_codes: list[str]


class ResourcePeriodOut(BaseModel):
    kind: Literal["SLOT", "BREAK"]
    code: str | None = None
    label: str | None = None
    start_time: str
    end_time: str
    activity: ResourceActivityOut | None = None


class ResourceDayOut(BaseModel):
    resource_id: UUID
    resource_name: str
    academic_year: str
    day: str
    periods: list[ResourcePeriodOut]
```

### 7.4 Frontend Resource Search Page

```tsx
// frontend/src/pages/DirectorResourcePage.tsx

export function DirectorResourcePage() {
  const { resourceId } = useParams();
  const [day, setDay] = useState<DayName>(currentDayName());
  const [academicYear, setAcademicYear] = useState(PROTOTYPE_ACADEMIC_YEAR);
  const [showWeekly, setShowWeekly] = useState(false);
  
  const resourceQuery = useQuery({
    queryKey: ['resource', resourceId],
    queryFn: () => getResource(resourceId!),
  });
  
  const dayQuery = useQuery({
    queryKey: ['resource-timetable', resourceId, day, academicYear],
    queryFn: () => getResourceTimetableDay(resourceId!, day, academicYear),
  });
  
  // Render similar to DirectorTeacherPage but show:
  // - Resource name instead of teacher name
  // - Activities show teacher acronym + section + subject
  // - Daily/weekly views work identically
  
  return (
    <Shell>
      <div className="resource-header">
        <h1>{resourceQuery.data?.name}</h1>
        <span className="resource-type-badge">
          {resourceQuery.data?.resource_type}
        </span>
        <span className="resource-dept-badge">
          {resourceQuery.data?.department}
        </span>
      </div>
      
      {/* Day tabs + daily/weekly toggle */}
      {/* Availability grid shows FREE vs OCCUPIED (teacher + subject) */}
    </Shell>
  );
}
```

### 7.5 UI Requirements Compliance

**Requirement 4**: Director teacher view must NOT display "FREE" in every empty slot.

**Solution**:
```tsx
// For teacher availability (DirectorTeacherPage.tsx)
// Change from:
<div className="avail-status avail-status--free">
  <span className="avail-free-badge">FREE</span>  {/* REMOVE THIS */}
</div>

// To:
<div className="avail-status avail-status--free">
  {/* Empty div — styling shows it's free */}
</div>
```

```css
/* frontend/src/index.css */
.avail-row--free {
  background: #f9fafb;  /* Light gray background */
  border-left: 3px solid #10b981;  /* Green accent */
}

.avail-row--occupied {
  background: #fff;
  border-left: 3px solid #3b82f6;  /* Blue accent */
}
```

**12-hour format**: Already implemented in `formatTime()` helper (types.ts).

---


## 8. DIRECT EDITING + BULK REVIEW/PUBLISH ARCHITECTURE

### 8.1 Problem

Current workflow: Import → Preview → Confirm per teacher (POST `/teachers/{id}/timetable/confirm`).

**Doesn't scale** to 100+ teachers.

### 8.2 New Workflow

```
Upload DOCX/XLSX
  ↓
Preview all teachers + validation issues
  ↓
Fix errors (inline editing in preview UI)
  ↓
Save all as DRAFT (batch operation)
  ↓
Review department-wide
  ↓
Publish all valid DRAFTs → CONFIRMED (batch operation)
```

### 8.3 Backend: Bulk Confirm Endpoint

```python
# backend/app/api/imports.py (NEW endpoint)

@router.post(
    "/{import_id}/confirm-bulk",
    response_model=BulkConfirmOut,
    summary="Confirm import and publish all teachers to CONFIRMED in one transaction"
)
def confirm_bulk(
    import_id: str,
    publish: bool = Query(default=False, description="If true, promote DRAFTs to CONFIRMED"),
    db: Session = Depends(get_db)
) -> BulkConfirmOut:
    """
    Two-phase confirmation:
    
    Phase 1 (publish=False):
      - Validate
      - Persist as DRAFT for all teachers
      - Return summary
    
    Phase 2 (publish=True):
      - Validate (again, state may have changed)
      - Persist as DRAFT
      - Promote all DRAFTs to CONFIRMED
      - Return summary
    
    Transactional: all-or-nothing across ALL teachers.
    """
    preview = _STAGING.get(import_id)
    if not preview:
        raise HTTPException(404, "Import not found")
    
    # Re-validate
    engine = ValidationEngine(db)
    issues = engine.validate(preview)
    errors = [i for i in issues if i.severity == "ERROR"]
    
    if errors:
        raise HTTPException(422, detail={
            "message": "Validation errors prevent confirmation",
            "issues": [i.dict() for i in errors]
        })
    
    # Persist as DRAFT
    result = persist_import(preview, db, source="IMPORT")
    
    if publish:
        # Promote all created/replaced timetables to CONFIRMED
        timetable_ids = result["timetables_created"] + result["timetables_replaced"]
        for tt_id_str in timetable_ids:
            tt = db.scalar(select(Timetable).where(Timetable.id == UUID(tt_id_str)))
            if tt and tt.status == "DRAFT":
                # Run per-timetable validation (same as single-teacher confirm)
                validate_timetable_for_confirmation(tt, db)
                tt.status = "CONFIRMED"
                tt.last_verified_at = datetime.now(timezone.utc)
    
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, "Bulk confirmation failed, all changes rolled back") from exc
    
    _STAGING.pop(import_id, None)
    
    return BulkConfirmOut(
        import_id=import_id,
        academic_year=preview.academic_year,
        teachers_created=result["teachers_created"],
        teachers_reused=result["teachers_reused"],
        timetables_confirmed=timetable_ids if publish else [],
        timetables_draft=timetable_ids if not publish else []
    )
```

### 8.4 Frontend: Inline Editing in Preview

```tsx
// frontend/src/pages/ExcelImportPage.tsx (enhanced)

function ImportPreviewEditor({ preview }: { preview: ImportPreview }) {
  const [editedPreview, setEditedPreview] = useState(preview);
  const [selectedIssue, setSelectedIssue] = useState<ValidationIssue | null>(null);
  
  // Group issues by severity
  const errors = preview.validation_issues.filter(i => i.severity === 'ERROR');
  const warnings = preview.validation_issues.filter(i => i.severity === 'WARNING');
  
  return (
    <div className="import-preview">
      {/* Validation Issues Panel */}
      <div className="validation-panel">
        <h3>Validation Results</h3>
        {errors.length > 0 && (
          <div className="issue-group issue-group--error">
            <h4>{errors.length} Errors (must fix before confirming)</h4>
            {errors.map((issue, i) => (
              <ValidationIssueCard 
                key={i}
                issue={issue}
                onClick={() => setSelectedIssue(issue)}
              />
            ))}
          </div>
        )}
        {warnings.length > 0 && (
          <div className="issue-group issue-group--warning">
            <h4>{warnings.length} Warnings (review recommended)</h4>
            {warnings.map((issue, i) => (
              <ValidationIssueCard key={i} issue={issue} />
            ))}
          </div>
        )}
      </div>
      
      {/* Teachers Table (editable) */}
      <div className="teachers-table-section">
        <h3>Teachers ({preview.teachers.length})</h3>
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Acronym</th>
              <th>Department</th>
              <th>Program</th>
              <th>Action</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {editedPreview.teachers.map((t, i) => (
              <TeacherRowEditor
                key={i}
                teacher={t}
                onChange={(updated) => {
                  const newTeachers = [...editedPreview.teachers];
                  newTeachers[i] = updated;
                  setEditedPreview({ ...editedPreview, teachers: newTeachers });
                }}
              />
            ))}
          </tbody>
        </table>
      </div>
      
      {/* Schedule Preview (editable) */}
      <div className="schedule-preview-section">
        <h3>Schedule Entries</h3>
        {Object.entries(editedPreview.days).map(([day, rows]) => (
          <DayScheduleEditor
            key={day}
            day={day}
            rows={rows}
            onChange={(updatedRows) => {
              setEditedPreview({
                ...editedPreview,
                days: { ...editedPreview.days, [day]: updatedRows }
              });
            }}
          />
        ))}
      </div>
      
      {/* Action Buttons */}
      <div className="import-actions">
        <button 
          disabled={errors.length > 0}
          onClick={() => confirmBulk(preview.import_id, false)}
        >
          Save as DRAFT
        </button>
        <button 
          disabled={errors.length > 0}
          onClick={() => confirmBulk(preview.import_id, true)}
          className="btn-primary"
        >
          Save & Publish to CONFIRMED
        </button>
      </div>
    </div>
  );
}

function ValidationIssueCard({ issue, onClick }: { issue: ValidationIssue; onClick?: () => void }) {
  const severityClass = `issue-card--${issue.severity.toLowerCase()}`;
  
  return (
    <div className={`issue-card ${severityClass}`} onClick={onClick}>
      <div className="issue-header">
        <span className="issue-code">{issue.code}</span>
        <span className="issue-severity-badge">{issue.severity}</span>
      </div>
      <p className="issue-message">{issue.message}</p>
      {issue.affected_entities && Object.keys(issue.affected_entities).length > 0 && (
        <div className="issue-details">
          {Object.entries(issue.affected_entities).map(([key, value]) => (
            <span key={key} className="issue-detail-chip">
              {key}: {JSON.stringify(value)}
            </span>
          ))}
        </div>
      )}
      {issue.suggestion && (
        <p className="issue-suggestion">
          💡 {issue.suggestion}
        </p>
      )}
      {issue.source_locations.length > 0 && (
        <p className="issue-source">
          Source: {issue.source_locations.map(loc => loc.display_ref).join(', ')}
        </p>
      )}
    </div>
  );
}
```

### 8.5 Direct Timetable Editing (Rich Editor)

```tsx
// frontend/src/components/TimetableEditor.tsx (NEW)

interface TimetableEditorProps {
  teacherId: string;
  academicYear: string;
  initialData?: TimetableWriteResponse;
}

export function TimetableEditor({ teacherId, academicYear, initialData }: TimetableEditorProps) {
  const [days, setDays] = useState<TimetableWritePayload['days']>(initialData?.days ?? {});
  const [selectedDay, setSelectedDay] = useState<DayName>('monday');
  const [selectedSlot, setSelectedSlot] = useState<string | null>(null);
  const [showAddDialog, setShowAddDialog] = useState(false);
  
  const saveMutation = useMutation({
    mutationFn: () => {
      const payload: TimetableWritePayload = { academic_year: academicYear, days };
      return initialData
        ? updateTimetable(teacherId, payload)
        : createTimetable(teacherId, payload);
    },
    onSuccess: () => {
      // Refetch, show success toast
    }
  });
  
  const handleAddActivity = (entry: ScheduleEntryPayload) => {
    const dayEntries = days[selectedDay] ?? [];
    setDays({
      ...days,
      [selectedDay]: [...dayEntries, entry]
    });
    setShowAddDialog(false);
  };
  
  const handleEditActivity = (index: number, updated: ScheduleEntryPayload) => {
    const dayEntries = [...(days[selectedDay] ?? [])];
    dayEntries[index] = updated;
    setDays({ ...days, [selectedDay]: dayEntries });
  };
  
  const handleDeleteActivity = (index: number) => {
    const dayEntries = [...(days[selectedDay] ?? [])];
    dayEntries.splice(index, 1);
    setDays({ ...days, [selectedDay]: dayEntries });
  };
  
  const handleMoveActivity = (fromDay: DayName, toDay: DayName, index: number) => {
    const fromEntries = [...(days[fromDay] ?? [])];
    const [moved] = fromEntries.splice(index, 1);
    const toEntries = [...(days[toDay] ?? []), moved];
    setDays({
      ...days,
      [fromDay]: fromEntries,
      [toDay]: toEntries
    });
  };
  
  return (
    <div className="timetable-editor">
      {/* Day tabs */}
      <div className="day-tabs">
        {DAY_NAMES.map(day => (
          <button
            key={day}
            className={day === selectedDay ? 'active' : ''}
            onClick={() => setSelectedDay(day)}
          >
            {DAY_LABELS[day]}
          </button>
        ))}
      </div>
      
      {/* Grid view with add/edit/delete controls */}
      <div className="schedule-grid-editor">
        {FALLBACK_PERIODS.map(period => {
          if (period.kind === 'BREAK') {
            return <div key={period.label} className="break-row">{period.label}</div>;
          }
          
          const dayEntries = days[selectedDay] ?? [];
          const activity = dayEntries.find(e => e.slot_ids.includes(period.code!));
          const activityIndex = activity ? dayEntries.indexOf(activity) : -1;
          
          return (
            <div 
              key={period.code}
              className={`slot-row ${activity ? 'occupied' : 'free'} ${selectedSlot === period.code ? 'selected' : ''}`}
              onClick={() => setSelectedSlot(period.code!)}
            >
              <span className="slot-time">{formatTimeRange(period.start_time, period.end_time)}</span>
              <span className="slot-code">{period.code}</span>
              
              {activity ? (
                <div className="activity-cell">
                  <span className="activity-type">{activity.entry_type}</span>
                  <span className="activity-subject">{activity.subject_or_activity || activity.entry_type}</span>
                  <span className="activity-meta">{activity.section} · {activity.room}</span>
                  <div className="activity-actions">
                    <button onClick={() => handleEditActivity(activityIndex, activity)}>Edit</button>
                    <button onClick={() => handleDeleteActivity(activityIndex)}>Delete</button>
                    <button onClick={() => /* show move dialog */}>Move</button>
                  </div>
                </div>
              ) : (
                <button
                  className="add-activity-btn"
                  onClick={() => setShowAddDialog(true)}
                >
                  + Add Activity
                </button>
              )}
            </div>
          );
        })}
      </div>
      
      {/* Save button */}
      <button
        className="btn-primary"
        onClick={() => saveMutation.mutate()}
        disabled={saveMutation.isPending}
      >
        Save Draft
      </button>
      
      {/* Add/Edit Dialog */}
      {showAddDialog && (
        <ActivityDialog
          slot={selectedSlot}
          onSave={handleAddActivity}
          onCancel={() => setShowAddDialog(false)}
        />
      )}
    </div>
  );
}
```

### 8.6 Validation on Edit

Client-side validation (instant feedback):
```typescript
function validateActivity(entry: ScheduleEntryPayload, existingEntries: ScheduleEntryPayload[]): string[] {
  const errors: string[] = [];
  
  // LAB must be 2 consecutive slots
  if (entry.entry_type === 'LAB') {
    if (!isValidLabPair(entry.slot_ids)) {
      errors.push('LAB must occupy exactly 2 consecutive slots');
    }
  }
  
  // No duplicate slots in same day
  const allSlots = existingEntries.flatMap(e => e.slot_ids);
  const duplicates = entry.slot_ids.filter(s => allSlots.includes(s));
  if (duplicates.length > 0) {
    errors.push(`Slot(s) ${duplicates.join(', ')} already occupied`);
  }
  
  return errors;
}
```

Server-side validation (on save):
- Same Pydantic `TimetableWriteIn` validation
- Plus ValidationEngine checks (teacher overlap, resource conflict)

---


## 9. COMPREHENSIVE EDGE-CASE MATRIX

### 9.1 File Edge Cases

| Case | Detection Point | Severity | Blocks Import? | User Message | Recovery |
|------|----------------|----------|----------------|--------------|----------|
| Wrong extension (.txt, .pdf, .jpg) | Upload validation | ERROR | Yes | "Only .xlsx and .docx files accepted" | Re-upload correct file |
| Mismatched content (renamed .txt to .xlsx) | Parser (openpyxl exception) | ERROR | Yes | "File appears corrupt or not a valid Excel file" | Upload genuine .xlsx |
| Corrupt XLSX (truncated bytes) | Parser (zipfile exception) | ERROR | Yes | "File is corrupt and cannot be opened" | Re-download/re-export file |
| Corrupt DOCX (invalid XML) | Parser (lxml exception) | ERROR | Yes | "File is corrupt or malformed" | Re-save in Word |
| Empty file (0 bytes) | Upload validation | ERROR | Yes | "Uploaded file is empty" | Upload non-empty file |
| XLSX with no sheets | Parser | ERROR | Yes | "Workbook contains no sheets" | Add required sheets |
| XLSX missing Teachers sheet | Parser | ERROR | Yes | "Required 'Teachers' sheet not found" | Add Teachers sheet |
| XLSX missing Schedule sheet | Parser | ERROR | Yes | "Required 'Schedule' sheet not found" | Add Schedule sheet |
| DOCX with no tables | Parser | ERROR | Yes | "No tables found in document" | Add table with timetable |
| Duplicate header rows | Parser | WARNING | No | "Multiple header rows detected, using first" | Review headers |

### 9.2 Teacher Edge Cases

| Case | Detection Point | Severity | Blocks? | Message | Recovery |
|------|----------------|----------|---------|---------|----------|
| Missing name | Normalizer | ERROR | Yes | "Teacher row 3: name is required" | Add teacher name |
| Blank name (whitespace only) | Normalizer | ERROR | Yes | "Teacher row 5: name cannot be blank" | Enter valid name |
| Missing acronym | Normalizer | ERROR | Yes | "Teacher row 7: acronym is required" | Add acronym |
| Duplicate acronym (same dept) | Validator (DB check) | CONFLICT | Yes | "Acronym 'DNS' already exists in Computer Applications" | Change acronym or mark REUSE |
| Duplicate acronym (diff dept) | Validator | INFO | No | "Acronym 'DNS' exists in another department" | Proceed (department-scoped identity) |
| Same acronym in import (same dept) | Normalizer | ERROR | Yes | "Duplicate acronym 'XYZ' in rows 3 and 7" | Fix duplicate |
| Teacher has no schedule rows | Validator | INFO | No | "Teacher 'ABC' has no schedule entries" | Add schedule or ignore |
| Invalid department | Validator | WARNING | No | "Department 'XYZ' not found, will be created" | Correct dept name or proceed |
| Invalid program | Validator (DB check) | ERROR | Yes | "Program 'ABC' not found for UG level" | Use valid program |
| Invalid level (not UG/PG) | Normalizer | ERROR | Yes | "Level must be 'UG' or 'PG', got 'Graduate'" | Fix to UG or PG |
| Invalid semester (zero, negative) | Normalizer | ERROR | Yes | "Semester must be positive, got 0" | Enter valid semester |
| Semester non-numeric | Normalizer | ERROR | Yes | "Semester must be a number, got 'First'" | Use numeric semester |
| Teacher inactive in DB | Validator | WARNING | No | "Teacher 'DNS' is marked inactive" | Reactivate or proceed |
| Resolved teacher ID mismatch | Validator | ERROR | Yes | "Teacher 'DNS' name mismatch: expected 'Alice', got 'Bob'" | Fix name or acronym |

### 9.3 Schedule Edge Cases

| Case | Detection Point | Severity | Blocks? | Message | Recovery |
|------|----------------|----------|---------|---------|----------|
| Invalid day ("Sundae") | Normalizer | ERROR | Yes | "Invalid day 'Sundae' in row 12" | Fix typo |
| Sunday schedule | Normalizer | ERROR | Yes | "Sunday is not a working day" | Remove or change day |
| Invalid time format | time_to_slots | ERROR | Yes | "Time '3:00 PM' does not match institutional slots" | Use slot codes or valid times |
| Time not matching slots | time_to_slots | ERROR | Yes | "Time '8:00 AM - 9:00 AM' does not match S1 (8:00-8:55)" | Fix time |
| Reversed time (end < start) | Normalizer | ERROR | Yes | "End time before start time" | Swap start/end |
| Break time used | Normalizer | ERROR | Yes | "Cannot schedule during break (10:45-11:15)" | Use valid slot |
| Lunch time used | Normalizer | ERROR | Yes | "Cannot schedule during lunch (13:05-14:00)" | Use valid slot |
| LAB one slot | Validator | ERROR | Yes | "LAB must occupy exactly 2 consecutive slots, got 1" | Add second slot |
| LAB three slots | Validator | ERROR | Yes | "LAB must occupy exactly 2 consecutive slots, got 3" | Remove extra slot |
| LAB non-consecutive | Validator | ERROR | Yes | "LAB slots S1, S4 are not consecutive" | Use consecutive pair |
| LAB crosses break | Validator | ERROR | Yes | "LAB slots S3-S4 cross break boundary" | Use valid pair (S2-S3 or S4-S5) |
| LAB crosses lunch | Validator | ERROR | Yes | "LAB slots S5-S6 cross lunch boundary" | Use valid pair |
| Duplicate slot same day/teacher | Validator | ERROR | Yes | "Teacher 'DNS' thursday S2: occupied by 2 activities" | Remove duplicate |
| Teacher overlap | Cross-validator | ERROR | Yes | "Teacher 'DNS' overlap: S2 monday in 2 activities" | Reschedule one activity |
| Missing teacher acronym in schedule | Normalizer | ERROR | Yes | "Schedule row 15: teacher acronym required" | Add teacher |
| Unknown teacher acronym | Validator | ERROR | Yes | "Teacher 'XYZ' not found in Teachers sheet" | Add to Teachers or fix acronym |
| Missing time AND slots | Parser | ERROR | Yes | "Schedule row 20: must have 'Time' or 'Slots' column" | Add required column |
| Time and Slots contradict | Normalizer | ERROR | Yes | "Time says S1-S2 but Slots says S3" | Fix inconsistency |

### 9.4 Resource Edge Cases

| Case | Detection Point | Severity | Blocks? | Message | Recovery |
|------|----------------|----------|---------|---------|----------|
| Unknown room | Resource validator | WARNING | No | "Room 'Lab XYZ' not found, will be auto-created" | Add resource or proceed |
| Room alias mismatch | Resource validator | WARNING | No | "Room 'Lab1A' might be alias of 'Lab 1A'" | Clarify or proceed |
| Resource overlap | Cross-validator | ERROR | Yes | "Lab1A thursday S3: used by 2 activities" | Reschedule one |
| Resource inactive | Resource validator | WARNING | No | "Room 'CA1' is marked inactive" | Reactivate or change room |
| Blank room field | Normalizer | INFO | No | "Activity has no room assigned" | Add room or proceed |

### 9.5 DOCX-Specific Edge Cases

| Case | Detection Point | Severity | Blocks? | Message | Recovery |
|------|----------------|----------|---------|---------|----------|
| Merged cell spans incompatible fields | Geometry reconstructor | ERROR | Yes | "Table 2: merged cell spans day + time columns ambiguously" | Unmerge or clarify layout |
| No clear day column | Row classifier | ERROR | Yes | "Table 1: cannot identify day column" | Add or label day column |
| Multiple possible teacher assignments | Field extractor | ERROR | Yes | "Table 3 row 5: ambiguous teacher (2 candidates)" | Clarify structure |
| Faculty name spans across sections | Field extractor | WARNING | No | "Teacher 'DNS' spans multiple sections, assuming same teacher" | Verify or split |
| vMerge chain broken | Geometry reconstructor | ERROR | Yes | "Table vertical merge inconsistent at row 8" | Fix merge in Word |
| gridSpan exceeds table width | Geometry reconstructor | ERROR | Yes | "Table horizontal span overflows at row 3" | Fix merge in Word |
| Table with no header row | Row classifier | ERROR | Yes | "Cannot identify header row in table" | Add header row |
| Multi-table DOCX with mixed formats | Parser | WARNING | No | "Multiple table formats detected, using first compatible" | Standardize tables |

### 9.6 Editing Edge Cases

| Case | Detection Point | Severity | Blocks? | Message | Recovery |
|------|----------------|----------|---------|---------|----------|
| Edit creates teacher conflict | Client validation | ERROR | Yes | "Slot S2 already occupied" | Choose different slot |
| Edit creates resource conflict | Server validation | ERROR | Yes | "Lab1A already booked for S3" | Choose different resource/slot |
| Moving activity to occupied slot | Client validation | ERROR | Yes | "Target slot already occupied" | Choose free slot |
| Deleting last activity for teacher | Client validation | INFO | No | "This will leave teacher with no schedule" | Confirm or cancel |
| Editing CONFIRMED timetable | API guard | ERROR | Yes | "Cannot edit CONFIRMED timetable directly" | Create new DRAFT or withdraw |
| Stale DRAFT (concurrent update) | Optimistic locking | ERROR | Yes | "Timetable was modified by another user" | Reload and retry |
| Saving with validation errors | Client validation | ERROR | Yes | "Fix 3 errors before saving" | Fix issues |
| Publishing with teacher overlap | Server validation | ERROR | Yes | "Cannot publish: teacher overlap detected" | Fix overlap first |

### 9.7 Availability Edge Cases

| Case | Detection Point | Severity | User Experience |
|------|----------------|----------|-----------------|
| No CONFIRMED timetable | API (404) | INFO | Show "No confirmed data" message, all slots shown as unknown |
| CONFIRMED timetable with no entries | API (empty periods) | INFO | Show all slots as FREE |
| Teacher search returns no results | API (empty list) | INFO | "No teachers found matching '{query}'" |
| Resource search returns no results | API (empty list) | INFO | "No resources found matching '{query}'" |
| Free teacher (no activities today) | Computed | INFO | Show all 9 slots as FREE (9 Free, 0 Occupied) |
| Fully occupied teacher | Computed | INFO | Show 0 Free, 9 Occupied |
| Teacher has activity but resource unresolved | Display | INFO | Show activity with "Room: Unknown" |

---


## 10. DATABASE CHANGES SUMMARY

### 10.1 New Tables

```sql
-- Resources (rooms/labs)
CREATE TABLE resources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('LAB', 'CLASSROOM', 'SEMINAR_HALL', 'AUDITORIUM', 'OTHER')),
    department TEXT NOT NULL DEFAULT 'Computer Applications',
    capacity SMALLINT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_resources_normalized_name_dept
    ON resources (lower(btrim(normalized_name)), lower(btrim(department)));

CREATE INDEX idx_resources_normalized_name
    ON resources (lower(btrim(normalized_name)));

-- Resource aliases
CREATE TABLE resource_aliases (
    resource_id UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    PRIMARY KEY (resource_id, alias)
);

CREATE INDEX idx_resource_aliases_alias
    ON resource_aliases (lower(btrim(alias)));
```

### 10.2 Schema Modifications

```sql
-- Add resource_id FK to schedule_entries
ALTER TABLE schedule_entries
    ADD COLUMN resource_id UUID REFERENCES resources(id);

-- Create index for resource availability queries
CREATE INDEX idx_schedule_entries_resource_day
    ON schedule_entries (resource_id, day_of_week)
    WHERE resource_id IS NOT NULL;

-- Teacher identity: drop global unique, add department-scoped unique
DROP INDEX IF EXISTS uq_teachers_acronym_normalized;

CREATE UNIQUE INDEX uq_teachers_acronym_dept_normalized
    ON teachers (lower(btrim(acronym)), lower(btrim(department)));

CREATE INDEX idx_teachers_acronym_all_depts
    ON teachers (lower(btrim(acronym)));
```

### 10.3 Migration Script

```sql
-- database/migrations/002_phase2_schema.sql

-- Phase 1: Add resource infrastructure
CREATE TABLE resources (...);  -- as above
CREATE TABLE resource_aliases (...);  -- as above

-- Phase 2: Add resource_id column (nullable initially)
ALTER TABLE schedule_entries ADD COLUMN resource_id UUID REFERENCES resources(id);

-- Phase 3: Backfill resources from existing room text
-- (Run Python migration script to parse room strings, create Resource records, update schedule_entries)

-- Phase 4: Update teacher identity constraint
DROP INDEX uq_teachers_acronym_normalized;
CREATE UNIQUE INDEX uq_teachers_acronym_dept_normalized
    ON teachers (lower(btrim(acronym)), lower(btrim(department)));
CREATE INDEX idx_teachers_acronym_all_depts ON teachers (lower(btrim(acronym)));

-- Phase 5: (Later) Make resource_id NOT NULL after all imports use it
-- ALTER TABLE schedule_entries ALTER COLUMN resource_id SET NOT NULL;
-- ALTER TABLE schedule_entries DROP COLUMN room;  -- (breaking change)
```

### 10.4 Migration Python Script

```python
# database/migrations/backfill_resources.py

from sqlalchemy import create_engine, select
from app.models.models import ScheduleEntry, Resource, Timetable, Teacher
import re

def normalize_room_name(text: str) -> str:
    """Normalize room names: 'Lab 1A' -> 'lab1a'"""
    return re.sub(r'\\s+', '', text.lower().strip())

def backfill_resources(db_url: str):
    engine = create_engine(db_url)
    with Session(engine) as session:
        # Get all unique room texts with their departments
        entries = session.scalars(
            select(ScheduleEntry)
            .join(Timetable)
            .join(Teacher)
            .where(ScheduleEntry.room.isnot(None), ScheduleEntry.room != '')
        ).all()
        
        room_dept_map: dict[tuple[str, str], str] = {}  # (normalized_name, dept) -> original_name
        for entry in entries:
            if not entry.room:
                continue
            dept = entry.timetable.teacher.department
            normalized = normalize_room_name(entry.room)
            room_dept_map[(normalized, dept)] = entry.room
        
        # Create Resource records
        resource_map: dict[tuple[str, str], UUID] = {}
        for (normalized, dept), original in room_dept_map.items():
            resource = Resource(
                id=uuid.uuid4(),
                name=original,
                normalized_name=normalized,
                resource_type=infer_resource_type(original),
                department=dept,
                is_active=True
            )
            session.add(resource)
            session.flush()
            resource_map[(normalized, dept)] = resource.id
        
        # Update schedule_entries with resource_id
        for entry in entries:
            if not entry.room:
                continue
            dept = entry.timetable.teacher.department
            normalized = normalize_room_name(entry.room)
            entry.resource_id = resource_map.get((normalized, dept))
        
        session.commit()
        print(f"Created {len(resource_map)} resources, updated {len(entries)} schedule entries")

def infer_resource_type(name: str) -> str:
    """Guess resource type from name"""
    lower = name.lower()
    if 'lab' in lower:
        return 'LAB'
    if 'hall' in lower or 'auditorium' in lower:
        return 'SEMINAR_HALL'
    return 'CLASSROOM'
```

---


## 11. API CHANGES SUMMARY

### 11.1 New Endpoints

```
POST   /api/v1/imports/docx              Upload DOCX file for preview
POST   /api/v1/imports/{id}/confirm-bulk  Bulk confirm with optional publish
GET    /api/v1/resources                 List resources (filterable)
GET    /api/v1/resources/search          Search resources by name
GET    /api/v1/resources/{id}            Get resource details
GET    /api/v1/resources/{id}/timetable  Get resource day schedule
POST   /api/v1/resources                 Create resource (admin)
PUT    /api/v1/resources/{id}            Update resource (admin)
```

### 11.2 Modified Endpoints

```
GET    /api/v1/teachers/search?q=&department=  Add optional department filter
POST   /api/v1/imports/excel                   Enhanced validation response
```

### 11.3 Enhanced Response Schemas

**ImportPreview** (modified):
```json
{
  "import_id": "uuid",
  "academic_year": "2025-2026",
  "department": "Computer Applications",
  "teachers": [...],
  "days": {...},
  "validation_issues": [
    {
      "severity": "ERROR",
      "code": "TEACHER_OVERLAP",
      "message": "Teacher DNS has overlapping activities",
      "source_locations": [
        {
          "file_type": "DOCX",
          "table_index": 2,
          "table_row_index": 5,
          "display_ref": "Table 2, Row 5"
        }
      ],
      "affected_entities": {
        "teacher": "DNS",
        "day": "thursday",
        "slot": "S2"
      },
      "suggestion": "Reschedule one of the conflicting activities"
    }
  ],
  "warnings": [],
  "errors": []
}
```

### 11.4 Breaking Changes

**Phase 2A** (Non-breaking):
- New endpoints added
- Existing endpoints return additional fields (backward compatible)
- `teacher.department` already exists (no schema change)

**Phase 2B** (After migration, breaking):
- `schedule_entries.room` deprecated in favor of `resource_id`
- Frontend must use `resource_id` instead of free-text `room`

### 11.5 API Versioning Strategy

Keep `/api/v1` for Phase 2A (non-breaking changes).

When ready for Phase 2B breaking changes:
- Introduce `/api/v2` with `resource_id` required
- Deprecate `room` field
- Keep `/api/v1` active for 3 months with deprecation warnings
- Sunset `/api/v1` after migration window

---


## 12. IMPLEMENTATION PLAN

### 12.1 Wave 1: Foundation (Weeks 1-2)

**Koushik Tasks**:
1. **DOCX Parser** (`backend/app/services/docx_import/`)
   - [ ] `parser.py`: extract tables from DOCX
   - [ ] `table_geometry.py`: reconstruct grid from merged cells (vMerge, gridSpan)
   - [ ] `row_classifier.py`: identify header/teacher/schedule rows
   - [ ] `field_extractor.py`: extract teacher/day/time/activity fields
   - [ ] `normalizer.py`: produce canonical `ScheduleImportRow`
   - [ ] Unit tests: 15 test cases covering merges, ambiguity, multi-teacher tables
   - [ ] Integration test: upload sample DOCX → preview

**Lipika Tasks**:
1. **UI Polish: Remove "FREE" Display**
   - [ ] Modify `DirectorTeacherPage.tsx`: remove FREE badge from empty slots
   - [ ] Update CSS: style free slots with subtle background/border
   - [ ] Verify 12-hour format (already implemented, test end-to-end)
   - [ ] Test daily + weekly views

2. **Department Filter UI**
   - [ ] Add department dropdown to Director search
   - [ ] Wire up to `searchTeachers(query, department)` API
   - [ ] Test cross-department search

**Dependencies**: None (parallel work)

**Deliverable**: DOCX import works end-to-end, UI shows availability without "FREE" text

---

### 12.2 Wave 2: Validation Engine + Resources (Weeks 3-4)

**Koushik Tasks**:
1. **Validation Engine** (`backend/app/services/validation/`)
   - [ ] `engine.py`: orchestrator
   - [ ] `teacher_validator.py`: teacher-specific rules
   - [ ] `resource_validator.py`: resource resolution + conflicts
   - [ ] `schedule_validator.py`: schedule structure rules
   - [ ] `cross_validator.py`: teacher overlap, resource overlap detection
   - [ ] `issue_formatter.py`: convert to `ValidationIssue` schema
   - [ ] Integrate with `resolve_and_validate()` in imports flow
   - [ ] 25+ unit tests covering all edge cases from matrix
   - [ ] Integration test: upload file with errors → validation issues returned

2. **Resource Model**
   - [ ] Create `resources` table schema
   - [ ] Create `resource_aliases` table
   - [ ] Add `resource_id` column to `schedule_entries` (nullable)
   - [ ] Implement `Resource` SQLAlchemy model
   - [ ] Write migration script: backfill from existing `room` text
   - [ ] Test migration on copy of production data

**Lipika Tasks**:
1. **Resource Availability**
   - [ ] Create `backend/app/api/resources.py` router
   - [ ] Implement `GET /resources`, `GET /resources/search`, `GET /resources/{id}/timetable`
   - [ ] Create `ResourceOut`, `ResourceDayOut`, `ResourcePeriodOut` schemas
   - [ ] Create `frontend/src/pages/DirectorResourcePage.tsx`
   - [ ] Add resource search UI (similar to teacher search)
   - [ ] Render resource daily/weekly availability
   - [ ] Test: search "Lab1A" → view availability → shows occupying teachers

2. **Validation Issues UI**
   - [ ] Create `ValidationIssueCard` component
   - [ ] Update `ExcelImportPage.tsx` to display structured validation issues
   - [ ] Group by severity (ERROR/WARNING/INFO)
   - [ ] Show source locations, affected entities, suggestions
   - [ ] Test: upload file with overlaps → errors displayed with details

**Dependencies**:
- Koushik must finish `ValidationEngine` before Lipika can wire up issue display
- Resource model must exist before Lipika can build resource availability UI

**Deliverable**: Validation engine detects all issues, resources are queryable, resource availability view works

---

### 12.3 Wave 3: Identity + Editing (Weeks 5-6)

**Koushik Tasks**:
1. **Teacher Identity Migration**
   - [ ] Drop `uq_teachers_acronym_normalized` index
   - [ ] Create `uq_teachers_acronym_dept_normalized` composite index
   - [ ] Update `resolve_teacher()` to use department scope
   - [ ] Modify `POST /teachers` to allow duplicate acronyms across departments
   - [ ] Update import validator to use department-scoped resolution
   - [ ] Migration audit script: check for existing cross-dept duplicates
   - [ ] Test: create two teachers with same acronym, different departments → success

2. **Bulk Confirm Endpoint**
   - [ ] Implement `POST /imports/{id}/confirm-bulk?publish=true/false`
   - [ ] Transactional: all teachers succeed or all rollback
   - [ ] Re-validate before persist
   - [ ] Optional publish phase: promote DRAFT → CONFIRMED
   - [ ] Return `BulkConfirmOut` with summary
   - [ ] Integration test: bulk confirm 15 teachers → all CONFIRMED

**Lipika Tasks**:
1. **Direct Timetable Editor**
   - [ ] Create `TimetableEditor.tsx` component
   - [ ] Grid view with add/edit/delete/move controls
   - [ ] `ActivityDialog` for adding new activities
   - [ ] Client-side validation (instant feedback)
   - [ ] Wire up to `POST /teachers/{id}/timetable` (create) and `PUT` (update)
   - [ ] Test: add activity → save → reload → edit activity → delete activity

2. **Bulk Review UI**
   - [ ] Enhance `ExcelImportPage.tsx` with inline editing
   - [ ] Teachers table: editable name/acronym/department
   - [ ] Schedule preview: editable day/time/activity
   - [ ] "Save as DRAFT" button (publish=false)
   - [ ] "Save & Publish" button (publish=true, disabled if errors)
   - [ ] Test: upload → fix errors inline → publish → verify CONFIRMED

**Dependencies**:
- Bulk confirm endpoint must exist before Lipika can wire UI
- Koushik's identity migration must complete before Lipika can test cross-dept teachers

**Deliverable**: Users can edit timetables directly in UI, bulk publish works, department-scoped identity functional

---

### 12.4 Wave 4: Integration + Testing (Week 7)

**Joint Tasks**:
1. **End-to-End Testing**
   - [ ] Full DOCX import → validation → fix → bulk publish flow
   - [ ] Full XLSX import → validation → fix → bulk publish flow
   - [ ] Teacher availability with resource conflicts
   - [ ] Resource availability showing teacher assignments
   - [ ] Direct editing creating teacher overlap → blocked by validation
   - [ ] Cross-department teachers with same acronym
   - [ ] Edge case matrix: test 50+ scenarios from section 9

2. **Performance Testing**
   - [ ] Import 100 teachers (large DOCX)
   - [ ] Bulk publish 100 teachers (transaction time)
   - [ ] Resource availability query with 200+ schedule entries
   - [ ] Validation engine with 500+ schedule rows

3. **Documentation**
   - [ ] Update API.md with new endpoints
   - [ ] Write DOCX import guide (table structure requirements)
   - [ ] Write validation guide (error codes, recovery paths)
   - [ ] Update deployment docs with migration steps

4. **Bug Fixes & Polish**
   - [ ] Fix any issues discovered during testing
   - [ ] UI/UX refinements based on user feedback
   - [ ] Accessibility review (ARIA labels, keyboard navigation)

**Deliverable**: Production-ready Phase 2 system

---

## 13. DEPENDENCIES BETWEEN KOUSHIK AND LIPIKA

### 13.1 Koushik Blocking Lipika

| Koushik Deliverable | Lipika Dependency | When Needed |
|---------------------|-------------------|-------------|
| `ValidationIssue` schema | Validation issues UI | Wave 2 |
| `Resource` model + API | Resource availability UI | Wave 2 |
| Bulk confirm endpoint | Bulk publish UI | Wave 3 |
| Enhanced import preview schema | Inline editing UI | Wave 3 |

### 13.2 Lipika Blocking Koushik

| Lipika Deliverable | Koushik Dependency | When Needed |
|--------------------|-------------------|-------------|
| None | N/A | (Backend-first approach) |

### 13.3 Shared Deliverables

| Item | Owners | Coordination Required |
|------|--------|----------------------|
| `ValidationIssue` schema design | Both | Wave 2 kickoff |
| API contracts for resource endpoints | Both | Wave 2 kickoff |
| Bulk confirm request/response schema | Both | Wave 3 kickoff |
| Edge case test scenarios | Both | Ongoing |

---


## 14. RISKS & MITIGATION

### 14.1 Technical Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| DOCX merged-cell geometry reconstruction fails for complex layouts | High | Medium | Extensive unit tests, fallback to manual clarification, document supported layouts |
| Validation engine performance degrades with 500+ activities | Medium | Low | Optimize cross-validator with indexed lookups, batch queries, early-exit conditions |
| Resource backfill migration creates incorrect mappings | High | Medium | Audit script to review mappings before commit, manual review of ambiguous cases, rollback plan |
| Bulk publish transaction timeout (100+ teachers) | Medium | Medium | Batch in chunks of 25, show progress indicator, allow partial retry |
| Department-scoped identity breaks existing integrations | High | Low | Thorough testing of search/resolution paths, coordinate with any external systems |
| Frontend state management with inline editing becomes complex | Medium | Medium | Use controlled components, debounce validation, clear state boundaries |

### 14.2 Data Quality Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| DOCX tables have ambiguous layouts users didn't anticipate | High | Clear documentation of supported layouts, validation errors with specific guidance |
| Room name variations ("Lab1A" vs "Lab 1A") create duplicate resources | Medium | Normalization + alias system, admin UI to merge duplicates |
| Existing data has teacher overlaps that weren't caught | High | Run validation on existing CONFIRMED timetables before Phase 2 deploy |
| Users accidentally publish invalid data via bulk operation | High | Require explicit confirmation for bulk publish, show error count, block if errors exist |

### 14.3 User Experience Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Users confused by structured validation issues vs simple error messages | Medium | Clear issue cards with severity badges, actionable suggestions, example corrections |
| Inline editing in preview feels clunky | Medium | Usability testing, iterate on editor UX, provide "edit in full editor" option |
| Resource availability UI cluttered with too much info | Low | Focus on essential info (teacher acronym, subject, section), expandable details |
| Department filter overwhelming if 50+ departments | Low | Autocomplete dropdown, recently-used departments at top |

### 14.4 Project Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Koushik delayed on DOCX parser → blocks validation engine testing | Medium | Prioritize XLSX validation first, mock DOCX output for Lipika's testing |
| Integration issues discovered late (Wave 4) | High | Weekly integration checkpoints, shared staging environment, continuous deployment |
| Scope creep (users request PDF import, OCR, etc.) | Medium | Document Phase 3 roadmap separately, stay focused on Phase 2 requirements |
| Migration causes downtime | High | Blue-green deployment, run migration script on replica first, test rollback procedure |

---

## 15. OPEN QUESTIONS

### 15.1 DOCX Import

1. **Q**: Should we support multiple table formats in one DOCX (e.g., MCA format vs Commerce format)?  
   **A**: TBD — Phase 2A supports one canonical format, Phase 2B can add format detection.

2. **Q**: How to handle DOCX with embedded images, comments, tracked changes?  
   **A**: Ignore non-table content. If images/comments interfere with table parsing, return parse error.

3. **Q**: Should we extract academic year from DOCX metadata or require form field?  
   **A**: Require form field (consistent with XLSX). DOCX metadata is unreliable.

### 15.2 Validation

1. **Q**: Should resource overlap be ERROR or WARNING?  
   **A**: ERROR (blocks publish). Resource double-booking is a scheduling error.

2. **Q**: Should we validate DRAFT timetables or only on CONFIRMED promotion?  
   **A**: Validate on save (DRAFT) with WARNINGs, enforce on publish (CONFIRMED) with ERRORs.

3. **Q**: How to handle partial overlaps (e.g., LAB S2-S3 vs CLASS S2)?  
   **A**: Any shared slot = overlap. Detect at slot level, not entry level.

### 15.3 Resources

1. **Q**: Should we track resource capacity (max students)?  
   **A**: Phase 2A: optional field. Phase 2B: validation if needed.

2. **Q**: Should resources have department ownership or be shared across departments?  
   **A**: Department-scoped initially. Shared resources (auditoriums) marked with "Shared" department.

3. **Q**: How to handle movable resources (projectors, laptops)?  
   **A**: Out of scope for Phase 2. Consider Phase 3 equipment tracking.

### 15.4 Teacher Identity

1. **Q**: Should we show department in teacher autocomplete results?  
   **A**: Yes. Format: "DNS (Computer Applications)".

2. **Q**: How to handle teacher transfers between departments?  
   **A**: Update `teacher.department` field. Historical timetables retain old department context.

### 15.5 Bulk Operations

1. **Q**: Should bulk publish be all-or-nothing or allow partial success?  
   **A**: All-or-nothing initially. If performance issues arise, introduce chunked publish with rollback.

2. **Q**: Should we allow publishing subset of teachers from an import?  
   **A**: Phase 2A: all or none. Phase 2B: add per-teacher checkboxes if requested.

---

## 16. TESTING STRATEGY

### 16.1 Backend Unit Tests

**DOCX Parser** (30 tests):
- Simple table (no merges)
- Vertical merge (teacher spans multiple rows)
- Horizontal merge (section spans columns)
- LAB spanning two time rows
- Multiple teachers in one table
- Ambiguous layouts (expect parse errors)
- Invalid time strings
- Missing required fields
- Corrupt DOCX handling

**Validation Engine** (50 tests):
- Each validation rule from edge-case matrix
- Teacher overlap detection (various scenarios)
- Resource conflict detection
- LAB duration/consecutive validation
- Break/lunch crossing
- Department-scoped teacher resolution
- Resource alias matching
- Duplicate handling

**Resource Model** (15 tests):
- Create resource
- Resolve room name → resource ID
- Handle aliases
- Detect ambiguous names
- Department filtering
- Migration backfill logic

### 16.2 Backend Integration Tests

**Import Flow** (10 tests):
- Upload XLSX → preview → confirm → verify DB
- Upload DOCX → preview → confirm → verify DB
- Upload with errors → validation issues returned
- Bulk confirm with publish=false → all DRAFT
- Bulk confirm with publish=true → all CONFIRMED
- Concurrent imports (isolation)
- Import conflict handling
- Resource auto-creation during import
- Department-scoped teacher reuse
- Cross-validator catches overlaps

### 16.3 Frontend Unit Tests

**Components** (20 tests):
- `ValidationIssueCard` renders severity/code/message
- `TimetableEditor` add/edit/delete operations
- `ActivityDialog` validation (client-side)
- `ResourceSearchPage` search and filtering
- `DirectorTeacherPage` without "FREE" badges
- `DirectorResourcePage` shows occupying teachers
- Department filter dropdown
- Inline editing in import preview

### 16.4 End-to-End Tests

**Critical Paths** (8 tests):
1. **DOCX Import Flow**:
   - Upload DOCX → see preview with 15 teachers
   - Validation issues displayed
   - Fix errors inline
   - Save as DRAFT → verify DB
   - Publish → verify CONFIRMED

2. **XLSX Import Flow**:
   - Upload XLSX → preview → bulk publish → verify

3. **Teacher Availability**:
   - Search teacher → view daily → verify no "FREE" text → view weekly

4. **Resource Availability**:
   - Search Lab1A → view daily → shows 3 occupied slots with teacher names

5. **Direct Editing**:
   - Open teacher editor → add CLASS activity → save → reload → verify

6. **Validation Blocking**:
   - Upload file with teacher overlap → attempt publish → blocked with error

7. **Cross-Department Teachers**:
   - Create teacher "DNS" in Comp Apps
   - Create teacher "DNS" in Commerce
   - Both succeed, search shows both

8. **Resource Conflict**:
   - Assign Lab1A to two activities at same time → validation error

---


## 17. PHASE-BY-PHASE EXECUTION PLAN

### Phase 2A: Non-Breaking Additions (Weeks 1-4)

**Goal**: Add DOCX import, validation engine, resource model (nullable FK), department filtering — no breaking changes.

**Deliverables**:
- DOCX import fully functional
- Validation engine integrated
- Resources table exists, `resource_id` column added (nullable)
- Resource availability API + UI
- Department-scoped teacher search
- Bulk confirm endpoint
- Direct timetable editor
- UI polish (no "FREE" text)

**Deployment**:
- Run schema migrations (new tables, indexes)
- Run resource backfill migration
- Deploy backend + frontend
- No downtime required (backward compatible)

**Verification**:
- Upload DOCX → success
- Upload XLSX → still works
- Teacher search with department filter
- Resource search returns results
- Existing CONFIRMED timetables unchanged

---

### Phase 2B: Identity Migration (Week 5)

**Goal**: Switch to department-scoped teacher identity.

**Deliverables**:
- Drop `uq_teachers_acronym_normalized`
- Create `uq_teachers_acronym_dept_normalized`
- Update import validator
- Update teacher creation endpoint

**Deployment**:
1. Audit existing data for cross-department duplicates (expect none due to current constraint)
2. Run migration script
3. Deploy backend
4. Test: create cross-department duplicate → success

**Rollback Plan**:
- Re-create `uq_teachers_acronym_normalized` index
- Revert import validator code

**Risk**: Low (constraint relaxation, no data deletion)

---

### Phase 2C: Resource FK Enforcement (Week 6+)

**Goal**: Make `resource_id` required, deprecate `room` text field.

**Prerequisites**:
- All imports use resource resolution
- All direct edits use resource picker
- Resource backfill 100% complete

**Deliverables**:
- `ALTER TABLE schedule_entries ALTER COLUMN resource_id SET NOT NULL`
- Deprecate `room` column (keep for 1 release cycle)
- Update API to reject `room` in payloads

**Deployment**:
1. Verify no `schedule_entries` with NULL `resource_id`
2. Run migration
3. Deploy backend with `/api/v2` (breaking change)
4. Frontend uses `resource_id` exclusively

**Rollback Plan**:
- Revert to `/api/v1`
- Set `resource_id` back to nullable

**Risk**: Medium (breaking change, requires coordination)

---

## 18. SUCCESS METRICS

### 18.1 Functional Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| DOCX import success rate | >95% | Successful previews / total uploads |
| Validation error detection rate | 100% | All known issues caught by engine |
| Bulk publish success rate | >98% | Successful publishes / attempts |
| Resource conflict detection | 100% | Zero double-bookings in CONFIRMED |
| Teacher overlap detection | 100% | Zero overlaps in CONFIRMED |
| Import-to-publish time | <5 minutes | User timer: upload → published |

### 18.2 Performance Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| DOCX parse time (100 teachers) | <10 seconds | Backend parse duration |
| Validation time (500 activities) | <5 seconds | ValidationEngine.validate() duration |
| Bulk publish time (100 teachers) | <30 seconds | Transaction commit time |
| Resource availability query | <500ms | API response time |
| Teacher availability query | <500ms | API response time |

### 18.3 User Experience Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| User satisfaction (import workflow) | >4.0/5 | Post-release survey |
| Error recovery rate | >90% | Users who fix errors and succeed |
| Direct editing adoption | >50% | % of timetables edited manually |
| Resource search usage | >30% | % of director sessions using resource view |

---

## 19. FUTURE ENHANCEMENTS (Phase 3+)

**Not in Phase 2 scope**, but documented for roadmap:

1. **PDF Import**: OCR-based table extraction (complex, low accuracy)
2. **Multi-Department Bulk Import**: Upload one file with all departments
3. **Timetable Versioning**: Keep history of CONFIRMED timetables
4. **Conflict Resolution Wizard**: Guided UI for fixing overlaps
5. **Resource Booking**: Reserve resources outside timetable context
6. **Equipment Tracking**: Track movable assets (projectors, laptops)
7. **Capacity Validation**: Warn if room capacity < section size
8. **Auto-Scheduling**: AI-powered timetable generation
9. **Mobile App**: Native iOS/Android for on-the-go availability checks
10. **Notifications**: Alert teachers when their timetable is published

---

## 20. CONCLUSION

### 20.1 Summary

Phase 2 transforms the Teacher Availability System from a prototype into a production-ready department management tool. The architecture supports:

- **Unified Import**: DOCX and XLSX produce the same canonical model
- **Comprehensive Validation**: Structured error/warning/info system catches all issues
- **Scalable Identity**: Department-scoped teachers support multi-department deployments
- **Resource Management**: Normalized rooms/labs with conflict detection
- **Bulk Operations**: Import 100+ teachers in one workflow
- **Rich Editing**: Direct timetable manipulation with instant validation
- **Dual Availability**: Search teachers OR resources, view their schedules

### 20.2 Key Architectural Principles Preserved

1. **Separation of Concerns**: Parser → Normalizer → Validator → Persister pipeline maintained
2. **Transactional Safety**: All-or-nothing operations, DRAFT/CONFIRMED semantics respected
3. **Exact-Match APIs**: No ambiguous fallbacks or implicit defaults
4. **Type Safety**: Pydantic validation at boundaries, TypeScript on frontend
5. **Logical vs Physical**: Schedule entries (activities) remain separate from slot occupancy

### 20.3 Team Coordination

**Koushik** focuses on:
- Data pipeline (DOCX parsing, validation engine)
- Data model (resources, identity)
- API contracts

**Lipika** focuses on:
- User interfaces (availability views, editors)
- User workflows (bulk review, direct editing)
- User experience (validation display, error recovery)

**Shared**:
- Schema design
- API contracts
- Integration testing
- Production deployment

### 20.4 Recommended Next Step

**Review this document with both developers**, clarify open questions, finalize priorities, then proceed to Wave 1 implementation.

---

**END OF PHASE 2 ARCHITECTURE DOCUMENT**

