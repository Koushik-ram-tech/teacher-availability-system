# PHASE 2 ARCHITECTURE — Teacher Availability System

**Document Version**: 2.0 (CORRECTED)  
**Date**: 2026-09-10  
**Git Baseline**: commit `6ef827f` — "Implement timetable import and availability workflow"  
**Implementation**: Solo (Koushik)

---

## EXECUTIVE SUMMARY

This document defines the Phase 2 architecture for the Teacher Availability System based on teacher feedback from the working prototype. Phase 2 transforms the system from an Excel-only teacher availability tool into a comprehensive timetable and resource management system.

**Core Phase 2 Capabilities:**

1. **Single-File Import**: One file (DOCX OR XLSX) containing all timetable data
2. **Structure-Aware DOCX Parser**: Understands merged cells, table geometry, detects ambiguity
3. **Comprehensive Validation Engine**: Detects teacher/resource overlaps, schedule violations, data inconsistencies
4. **Department-Scoped Teacher Identity**: Same acronym can exist in different departments
5. **Resource Management**: Normalized rooms/labs with availability tracking
6. **Direct Timetable Editing**: Add/edit/delete activities with same validation as imports
7. **Bulk Review/Publish**: Department-wide validation and atomic publishing
8. **Dual Availability Views**: Search teachers OR resources, view schedules

**Critical Architectural Principles:**

- **No Guessing**: Ambiguous source data → explicit error, never silent inference
- **Canonical Domain Model**: All sources (DOCX/XLSX/Editor) → same validated representation
- **Single Validation Engine**: One engine for imports AND direct edits
- **Atomic Publishing**: Validate entire scope → publish all or nothing
- **Source Traceability**: Errors reference exact source location (table/row/cell or sheet/row/field)

**Implementation**: Solo developer (Koushik), dependency-driven sequencing

---

## 1. CURRENT BASELINE (Commit 6ef827f)

### 1.1 What Currently Works

**Database (6 tables)**:
```
programs              → Academic programs (UG/PG)
teachers              → Faculty records with acronym/department/program
time_slots            → Fixed S1-S9 institutional time blocks
timetables            → Version records (DRAFT/CONFIRMED per teacher/year)
schedule_entries      → ONE logical activity (CLASS/LAB/OTHER)
schedule_entry_slots  → Slot occupancy mapping (entry → time_slots)
```

**Domain Distinction** (PRESERVE THIS):
- `schedule_entries` = logical activity (e.g., "DBMS Lab")
- `schedule_entry_slots` = physical slot occupancy (e.g., S4, S5)
- A LAB spanning S4+S5 = ONE entry mapped to TWO slots

**Excel Import Contract**:
- Sheet 1: **Teachers** (name, acronym, level, program, department, semester_scope)
- Sheet 2: **Schedule** (teacher_acronym, day, type, time, subject_or_activity, section, room)
- Academic year: supplied by API form field (NOT read from workbook)

**Import Flow**:
```
Excel upload → parse → normalize → validate → preview → confirm → persist
```

**Key Behaviors**:
- Timetables created ONLY for teachers with schedule entries
- Teacher without schedule rows = no timetable record (intentional)
- DRAFT/CONFIRMED separation enforced
- Slot conflicts caught by deferred constraint triggers

**What Works Well**:
- Clean 3-stage pipeline: parser (structure) → normalizer (syntax) → validator (semantics)
- Transactional persistence: all-or-nothing per import
- Bulk insert optimization for schedule_entry_slots (recent fix)
- Exact-match API semantics (no fallbacks)

### 1.2 Current Limitations (Phase 2 Addresses These)

| Limitation | Impact | Phase 2 Solution |
|------------|--------|------------------|
| XLSX-only | Teachers use DOCX workflows | Structure-aware DOCX parser |
| Globally unique teacher acronym | Collisions across departments | Department-scoped identity |
| Free-text room field | No resource conflict detection | Normalized Resource entity |
| No teacher overlap detection | Invalid schedules pass validation | Cross-entity validation engine |
| No resource availability | Can't search labs/classrooms | Resource availability API + UI |
| Per-teacher confirm workflow | Doesn't scale to 100+ teachers | Bulk review/publish |
| No direct editing | Must re-upload to change one activity | Rich timetable editor |
| Limited error details | "Row 5 has error" without context | Source traceability with table/cell refs |

---

## 2. PROBLEM STATEMENT

### 2.1 Teacher Feedback

Teachers demonstrated the working prototype and provided feedback:

**What They Need**:
1. Upload ONE file (their existing DOCX timetables) — not separate teacher + schedule files
2. See ALL validation errors at once (teacher overlaps, room conflicts, schedule violations)
3. Fix errors inline without re-uploading
4. Publish entire department in one operation
5. Search resources (Lab1A, CA1, FDC) to see availability
6. Edit timetables directly in UI (add/move/delete activities)
7. Department-filtered search (same acronym in different departments)

**What Must NOT Happen**:
- System must NOT guess ambiguous mappings
- System must NOT auto-create resources without explicit approval
- System must NOT allow invalid schedules to be published
- System must NOT show "FREE" text in every empty slot (visual clutter)

### 2.2 Known Source Ambiguity

**Official MCA Timetable Issue**:
- Tuesday I-A S4+S5 block contains multiple parallel activities
- Only two room labels for multiple activities
- Source structure does NOT permit authoritative room→activity mapping
- **Architecture Requirement**: Flag this as ERROR, require manual resolution, NEVER guess

---


## 3. ARCHITECTURE PRINCIPLES

### 3.1 Core Principles (NON-NEGOTIABLE)

**1. No Guessing**

When source data is ambiguous:
- ❌ Do NOT invent teacher assignments
- ❌ Do NOT invent resource mappings
- ❌ Do NOT infer authoritative resource types from names
- ❌ Do NOT silently auto-create resources
- ✅ DO report explicit ERROR with actionable message
- ✅ DO require manual resolution through authorized workflow

**2. Single Source of Truth**

All data flows through ONE canonical domain model:
```
DOCX Parser    ┐
XLSX Parser    ├──→ CanonicalTimetable ──→ ValidationEngine ──→ DRAFT ──→ CONFIRMED
Editor Changes ┘
```

**NOT** this (WRONG):
```
ImportPreview ──→ ValidationEngine
EditorPayload ──→ DifferentValidation
```

**3. Parse vs Validation Errors**

**PARSE ERROR**: Source structure cannot be understood
- Example: DOCX table has broken vMerge chain
- Result: No canonical timetable can be produced
- User Action: Fix source file structure

**VALIDATION ERROR**: Source understood, but data semantically invalid
- Example: Teacher scheduled in two rooms at same time
- Result: Canonical timetable exists and can be previewed, but publish blocked
- User Action: Fix data conflict

**WARNING**: Data understandable, potentially acceptable, needs review

**INFO**: Informational notice

**4. Atomic Publishing**

Initial implementation:
```
Validate full publish scope
→ If any blocking ERROR: publish nothing
→ Otherwise: single transaction, publish everything
```

Do NOT casually introduce chunked publishing — that would change atomicity semantics.

**5. Single Validation Engine**

Direct editing MUST use the SAME validation engine as imports:
```
Editor mutation → CanonicalTimetable → ValidationEngine → persist DRAFT
```

NO duplicate validation logic for editing vs importing.

**6. Deterministic and Explainable**

Every validation result must reference:
- What failed (teacher/resource/day/slot)
- Where in source (DOCX table 2 row 5, or XLSX Schedule!12)
- Why it failed (rule violated)
- How to fix (actionable suggestion)

---

## 4. CANONICAL DOMAIN MODEL

### 4.1 The Problem with ImportPreview

**Current `ImportPreview`** (transport/API schema) is NOT a domain model:
- Mixes presentation concerns (row_ref strings)
- Tied to import workflow
- Doesn't represent direct editing
- Used as validation input → creates tight coupling

**Correct Separation**:
```
Domain Layer:        CanonicalTimetable (domain objects)
Application Layer:   ValidationEngine operates on CanonicalTimetable
Transport Layer:     ImportPreview (API response), EditorPayload (API request)
```

### 4.2 Canonical Timetable Domain Model

```python
# backend/app/domain/timetable.py (NEW)

from dataclasses import dataclass
from uuid import UUID

@dataclass(frozen=True)
class SourceLocation:
    """Where this data came from"""
    source_type: Literal["DOCX", "XLSX", "MANUAL"]
    
    # DOCX
    table_index: int | None = None
    table_row: int | None = None
    table_col: int | None = None
    
    # XLSX
    sheet_name: str | None = None
    row_number: int | None = None
    field_name: str | None = None
    
    def display_ref(self) -> str:
        """Human-readable reference"""
        if self.source_type == "DOCX":
            return f"Table {self.table_index}, Row {self.table_row}"
        elif self.source_type == "XLSX":
            return f"{self.sheet_name}!{self.row_number}"
        else:
            return "Manual Edit"


@dataclass(frozen=True)
class TeacherIdentity:
    """Department-scoped teacher identity"""
    acronym: str           # Normalized: "DNS"
    department: str        # "Computer Applications"
    name: str              # "Dr. Alice Smith"
    level: str             # "UG" | "PG"
    program_name: str      # "MCA"
    semester: int          # 1-8
    
    # Resolution
    resolved_teacher_id: UUID | None = None  # None = CREATE, UUID = REUSE
    
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class ResourceReference:
    """Explicit resource reference (NOT auto-created)"""
    name: str              # Display name from source: "Lab 1A"
    department: str        # Owning department
    
    # Resolution
    resolved_resource_id: UUID | None = None  # None = UNRESOLVED (ERROR)
    resolution_status: Literal["RESOLVED", "UNRESOLVED", "AMBIGUOUS"] = "UNRESOLVED"
    
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class ActivitySlotRange:
    """Slot occupancy for one activity"""
    slot_codes: tuple[str, ...]  # ("S4", "S5") for LAB


@dataclass(frozen=True)
class ScheduleActivity:
    """ONE logical activity"""
    teacher_identity: TeacherIdentity
    day: str  # "monday" ... "saturday"
    slot_range: ActivitySlotRange
    entry_type: Literal["CLASS", "LAB", "OTHER"]
    subject_or_activity: str | None = None
    section: str | None = None
    resource: ResourceReference | None = None
    notes: str | None = None
    
    source_location: SourceLocation | None = None


@dataclass
class CanonicalTimetable:
    """The authoritative domain model ALL sources produce"""
    academic_year: str
    department: str | None  # Optional department scope for entire import
    
    teachers: list[TeacherIdentity]
    activities: list[ScheduleActivity]
    
    # Metadata
    import_id: str | None = None  # For staged imports
    source_file_name: str | None = None
```

**Key Properties**:
- Immutable data classes (frozen=True) → safe to pass around
- Source traceability built-in
- Department-scoped identity
- Explicit resolution status (NOT implicit creation)
- Day-of-week as string (matches API contract)
- Slot codes as tuple (immutable)

### 4.3 Source Adapters

Each source produces `CanonicalTimetable`:

```python
# XLSX Adapter
def xlsx_to_canonical(
    file_bytes: bytes,
    academic_year: str
) -> CanonicalTimetable:
    raw = parse_workbook(file_bytes, academic_year)
    normalized = normalize(raw)
    return build_canonical(normalized)

# DOCX Adapter
def docx_to_canonical(
    file_bytes: bytes,
    academic_year: str
) -> CanonicalTimetable:
    tables = extract_docx_tables(file_bytes)
    geometry = reconstruct_geometry(tables)
    normalized = extract_canonical_data(geometry)
    return build_canonical(normalized, source_type="DOCX")

# Editor Adapter
def editor_payload_to_canonical(
    payload: TimetableWriteIn,
    teacher_id: UUID
) -> CanonicalTimetable:
    # Convert API payload → CanonicalTimetable
    # Source location = MANUAL
    ...
```

---


## 5. VALIDATION ARCHITECTURE

### 5.1 ValidationEngine Interface

```python
# backend/app/domain/validation.py (NEW)

@dataclass(frozen=True)
class ValidationIssue:
    severity: Literal["ERROR", "WARNING", "INFO"]
    code: str  # "TEACHER_OVERLAP", "RESOURCE_UNRESOLVED", etc.
    message: str  # Human-readable
    source_locations: tuple[SourceLocation, ...]  # Where the issue originated
    affected_entities: dict[str, Any]  # {teacher, day, slot, etc.}
    suggestion: str | None = None  # How to fix


class ValidationEngine:
    """Single engine for ALL validation (imports AND edits)"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def validate(self, canonical: CanonicalTimetable) -> list[ValidationIssue]:
        """
        Run all validation rules on canonical timetable.
        Returns: list of issues (ERROR/WARNING/INFO)
        """
        issues: list[ValidationIssue] = []
        
        # Phase 1: Structural validation
        issues.extend(self._validate_structure(canonical))
        
        # Phase 2: Teacher resolution/conflicts
        issues.extend(self._validate_teachers(canonical))
        
        # Phase 3: Resource resolution
        issues.extend(self._validate_resources(canonical))
        
        # Phase 4: Schedule rules (LAB duration, break crossing, etc.)
        issues.extend(self._validate_schedule_rules(canonical))
        
        # Phase 5: Cross-entity conflicts (teacher overlap, resource overlap)
        issues.extend(self._validate_overlaps(canonical))
        
        return issues
    
    def has_blocking_errors(self, issues: list[ValidationIssue]) -> bool:
        """Check if any ERROR exists (blocks publish)"""
        return any(i.severity == "ERROR" for i in issues)
```

### 5.2 Validation Rules (Comprehensive)

**Teacher Validation**:
| Rule | Code | Severity | Blocks Publish? |
|------|------|----------|-----------------|
| Missing teacher name | `TEACHER_MISSING_NAME` | ERROR | Yes |
| Missing teacher acronym | `TEACHER_MISSING_ACRONYM` | ERROR | Yes |
| Duplicate acronym (same dept) | `TEACHER_DUPLICATE_ACRONYM` | ERROR | Yes |
| Duplicate acronym (diff dept) | `TEACHER_ACRONYM_CROSS_DEPT` | INFO | No |
| Teacher has no activities | `TEACHER_NO_ACTIVITIES` | INFO | No |
| Invalid level (not UG/PG) | `TEACHER_INVALID_LEVEL` | ERROR | Yes |
| Invalid semester (<=0) | `TEACHER_INVALID_SEMESTER` | ERROR | Yes |
| Unknown program | `TEACHER_UNKNOWN_PROGRAM` | ERROR | Yes |
| Teacher inactive in DB | `TEACHER_INACTIVE` | WARNING | No |
| **Teacher overlap (same teacher, day, slot, different activities)** | `TEACHER_OVERLAP` | ERROR | Yes |

**Resource Validation**:
| Rule | Code | Severity | Blocks Publish? |
|------|------|----------|-----------------|
| **Resource unresolved** | `RESOURCE_UNRESOLVED` | ERROR | Yes |
| **Resource ambiguous (multiple matches)** | `RESOURCE_AMBIGUOUS` | ERROR | Yes |
| **Resource overlap (same resource, day, slot, different activities)** | `RESOURCE_OVERLAP` | ERROR | Yes |
| Resource inactive in DB | `RESOURCE_INACTIVE` | WARNING | No |

**Schedule Validation**:
| Rule | Code | Severity | Blocks Publish? |
|------|------|----------|-----------------|
| Invalid day | `SCHEDULE_INVALID_DAY` | ERROR | Yes |
| Sunday schedule | `SCHEDULE_SUNDAY` | ERROR | Yes |
| Invalid slot code | `SCHEDULE_INVALID_SLOT` | ERROR | Yes |
| LAB not exactly 2 consecutive slots | `SCHEDULE_LAB_INVALID_DURATION` | ERROR | Yes |
| LAB slots non-consecutive | `SCHEDULE_LAB_NON_CONSECUTIVE` | ERROR | Yes |
| LAB crosses break | `SCHEDULE_LAB_BREAK_CROSSING` | ERROR | Yes |
| LAB crosses lunch | `SCHEDULE_LAB_LUNCH_CROSSING` | ERROR | Yes |
| Slot occupied twice (same teacher/day) | `SCHEDULE_SLOT_COLLISION` | ERROR | Yes |
| Break/lunch used as activity slot | `SCHEDULE_BREAK_SLOT_USED` | ERROR | Yes |
| 3+ consecutive slots | `SCHEDULE_INVALID_DURATION` | ERROR | Yes |

**Source Validation**:
| Rule | Code | Severity | Blocks Publish? |
|------|------|----------|-----------------|
| Ambiguous source mapping | `SOURCE_AMBIGUOUS_MAPPING` | ERROR | Yes |
| Missing required field | `SOURCE_MISSING_FIELD` | ERROR | Yes |
| Malformed time string | `SOURCE_INVALID_TIME` | ERROR | Yes |

### 5.3 Teacher Overlap Detection

```python
def _validate_overlaps(self, canonical: CanonicalTimetable) -> list[ValidationIssue]:
    issues = []
    
    # Group activities by (teacher, day, slot)
    occupancy: dict[tuple[str, str, str], list[ScheduleActivity]] = {}
    
    for activity in canonical.activities:
        teacher_key = (activity.teacher_identity.acronym, activity.teacher_identity.department)
        for slot in activity.slot_range.slot_codes:
            key = (*teacher_key, activity.day, slot)
            occupancy.setdefault(key, []).append(activity)
    
    # Detect overlaps
    for key, activities in occupancy.items():
        if len(activities) > 1:
            teacher_acr, teacher_dept, day, slot = key
            issues.append(ValidationIssue(
                severity="ERROR",
                code="TEACHER_OVERLAP",
                message=(
                    f"Teacher {teacher_acr} ({teacher_dept}) has overlapping activities "
                    f"on {day} at {slot}"
                ),
                source_locations=tuple(
                    a.source_location for a in activities if a.source_location
                ),
                affected_entities={
                    "teacher_acronym": teacher_acr,
                    "department": teacher_dept,
                    "day": day,
                    "slot": slot,
                    "activities": [a.subject_or_activity for a in activities]
                },
                suggestion="Remove or reschedule one of the conflicting activities"
            ))
    
    # Resource overlap (similar logic, keyed by resource instead of teacher)
    ...
    
    return issues
```

### 5.4 Resource Resolution (NO AUTO-CREATE)

```python
def _validate_resources(self, canonical: CanonicalTimetable) -> list[ValidationIssue]:
    """
    Validate resource references.
    
    Rules:
    1. Known exact resource → RESOLVED (auto)
    2. Known unique alias → RESOLVED (auto)
    3. Unknown resource → UNRESOLVED (ERROR)
    4. Multiple matches → AMBIGUOUS (ERROR)
    5. Do NOT auto-create resources
    """
    issues = []
    
    for activity in canonical.activities:
        if not activity.resource:
            continue
        
        if activity.resource.resolution_status == "UNRESOLVED":
            issues.append(ValidationIssue(
                severity="ERROR",
                code="RESOURCE_UNRESOLVED",
                message=f"Resource '{activity.resource.name}' not found in database",
                source_locations=(activity.source_location,) if activity.source_location else (),
                affected_entities={
                    "resource_name": activity.resource.name,
                    "department": activity.resource.department,
                    "day": activity.day,
                    "teacher": activity.teacher_identity.acronym
                },
                suggestion=(
                    "Create this resource in the database first, or map it to an existing "
                    "resource alias"
                )
            ))
        
        if activity.resource.resolution_status == "AMBIGUOUS":
            issues.append(ValidationIssue(
                severity="ERROR",
                code="RESOURCE_AMBIGUOUS",
                message=f"Resource '{activity.resource.name}' matches multiple database entries",
                source_locations=(activity.source_location,) if activity.source_location else (),
                affected_entities={"resource_name": activity.resource.name},
                suggestion="Specify which resource you mean, or use a unique alias"
            ))
    
    return issues
```

---


## 6. DOCX IMPORT ARCHITECTURE

### 6.1 Strategy: Structure-Aware Parsing

**DO NOT** use text extraction + regex.

**DO** use WordprocessingML table structure:
- Access cell grid coordinates
- Detect `vMerge` (vertical cell merge)
- Detect `gridSpan` (horizontal cell span)
- Reconstruct logical table geometry
- Extract cell text with position context

### 6.2 DOCX Parser Phases

```
Phase 1: Table Extraction
  → Extract all tables from DOCX
  → Filter relevant timetable tables

Phase 2: Geometry Reconstruction
  → Build 2D cell grid
  → Resolve merged cells (vMerge, gridSpan)
  → Assign each data cell to grid coordinates

Phase 3: Row Classification
  → Identify HEADER rows
  → Identify TEACHER metadata rows
  → Identify SCHEDULE data rows
  → Identify EMPTY rows

Phase 4: Field Extraction
  → Extract teacher → acronym, department
  → Extract day → monday...saturday
  → Extract time → resolve to S1...S9
  → Extract activity → type, subject, section, room

Phase 5: Ambiguity Detection
  → Multiple teachers for one activity? → ERROR
  → Multiple rooms, one activity? → ERROR
  → Unclear day column? → ERROR
  → NEVER GUESS
```

### 6.3 Known Ambiguity Example

**Tuesday I-A S4+S5 Block** (from official MCA timetable):
```
Source structure:
  - Multiple parallel activities in S4+S5
  - Only TWO room labels for multiple activities
  - No clear 1:1 room→activity mapping

System behavior:
  → ValidationIssue(
      severity="ERROR",
      code="SOURCE_AMBIGUOUS_MAPPING",
      message="Cannot determine which room belongs to which activity",
      source_location=SourceLocation(table_index=2, table_row=15),
      suggestion="Split merged cells or add room labels for each activity"
    )
```

### 6.4 DOCX Parser Module Structure

```
backend/app/services/docx_import/
├── __init__.py
├── parser.py              # Entry: extract tables → RawDocxData
├── geometry.py            # Reconstruct cell grid from vMerge/gridSpan
├── classifier.py          # Classify rows (header/teacher/schedule)
├── extractor.py           # Extract fields from classified rows
├── normalizer.py          # RawDocxData → CanonicalTimetable
└── ambiguity.py           # Detect ambiguous mappings
```

**Example**:
```python
# backend/app/services/docx_import/parser.py

def parse_docx(file_bytes: bytes, academic_year: str) -> CanonicalTimetable:
    """
    Parse DOCX → CanonicalTimetable.
    Raises ParseError if structure cannot be understood.
    """
    doc = Document(BytesIO(file_bytes))
    tables = doc.tables
    
    if not tables:
        raise ParseError("No tables found in DOCX")
    
    # Reconstruct geometry
    grids = [reconstruct_geometry(table) for table in tables]
    
    # Classify rows
    classified = [classify_rows(grid) for grid in grids]
    
    # Extract data
    raw_data = extract_canonical_data(classified, source_type="DOCX")
    
    # Build canonical
    return build_canonical_timetable(raw_data, academic_year)
```

---

## 7. RESOURCE ARCHITECTURE

### 7.1 Resource Entity Design

```sql
-- database/schema_phase2.sql (additions)

CREATE TABLE IF NOT EXISTS resources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,                           -- Display: "Lab 1A"
    normalized_name TEXT NOT NULL,                -- Searchable: "lab1a"
    resource_type TEXT NOT NULL                   -- LAB, CLASSROOM, SEMINAR_HALL, etc.
        CHECK (resource_type IN ('LAB', 'CLASSROOM', 'SEMINAR_HALL', 'AUDITORIUM', 'OTHER')),
    department TEXT,                              -- Owning department (NULL = shared)
    capacity SMALLINT,                            -- Optional
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Unique within department (NULL department = globally unique shared resource)
CREATE UNIQUE INDEX uq_resources_normalized_name_dept
    ON resources (lower(btrim(normalized_name)), COALESCE(lower(btrim(department)), '_SHARED_'));

-- Resource aliases for "Lab1A" = "Lab 1A" = "CA Lab 1A"
CREATE TABLE IF NOT EXISTS resource_aliases (
    resource_id UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    PRIMARY KEY (resource_id, alias)
);

CREATE INDEX idx_resource_aliases_alias
    ON resource_aliases (lower(btrim(alias)));
```

### 7.2 Resource Scope Model

**Department-Owned Resources**:
- `department = "Computer Applications"` → CA-specific labs
- `department = "Commerce"` → Commerce-specific rooms

**Shared Resources**:
- `department = NULL` → Shared across all departments (e.g., auditoriums, seminar halls)
- Unique constraint uses `COALESCE(department, '_SHARED_')` to enforce uniqueness

**NOT this** (WRONG):
```sql
department = "Shared"  -- This is a fake department, don't do this
```

### 7.3 Resource Resolution Logic

```python
# backend/app/services/resource_resolver.py (NEW)

class ResourceResolver:
    def __init__(self, db: Session):
        self.db = db
    
    def resolve(
        self, name: str, department: str
    ) -> tuple[UUID | None, Literal["RESOLVED", "UNRESOLVED", "AMBIGUOUS"]]:
        """
        Try to resolve resource name → ID.
        
        Returns: (resource_id, status)
        
        RESOLVED: Exactly one match found
        UNRESOLVED: No match found
        AMBIGUOUS: Multiple matches found
        """
        normalized = normalize_resource_name(name)  # "Lab 1A" → "lab1a"
        
        # Try exact match in this department
        matches = db.scalars(
            select(Resource).where(
                func.lower(func.btrim(Resource.normalized_name)) == normalized,
                or_(
                    func.lower(func.btrim(Resource.department)) == department.lower(),
                    Resource.department.is_(None)  # Shared resource
                ),
                Resource.is_active.is_(True)
            )
        ).all()
        
        if len(matches) == 1:
            return matches[0].id, "RESOLVED"
        elif len(matches) == 0:
            # Try aliases
            alias_matches = db.scalars(
                select(Resource)
                .join(ResourceAlias)
                .where(
                    func.lower(func.btrim(ResourceAlias.alias)) == normalized,
                    or_(
                        func.lower(Resource.department) == department.lower(),
                        Resource.department.is_(None)
                    ),
                    Resource.is_active.is_(True)
                )
            ).all()
            
            if len(alias_matches) == 1:
                return alias_matches[0].id, "RESOLVED"
            elif len(alias_matches) == 0:
                return None, "UNRESOLVED"
            else:
                return None, "AMBIGUOUS"
        else:
            return None, "AMBIGUOUS"


def normalize_resource_name(text: str) -> str:
    """Normalize: 'Lab 1A' → 'lab1a'"""
    return re.sub(r'\s+', '', text.lower().strip())
```

### 7.4 Resource Type Inference (OPTIONAL UX HINT ONLY)

**DO NOT treat inferred type as authoritative**.

```python
def suggest_resource_type(name: str) -> str | None:
    """
    Optional heuristic suggestion for resource type.
    
    Returns:
      - 'LAB' if name suggests laboratory
      - 'SEMINAR_HALL' if name suggests hall/auditorium
      - None if no meaningful signal (user must select explicitly)
    
    CRITICAL:
      - This is an OPTIONAL UX hint only
      - NEVER authoritative
      - NEVER silently determines persisted resource_type
      - User must explicitly confirm/select type
    """
    lower = name.lower()
    if 'lab' in lower:
        return 'LAB'
    if 'hall' in lower or 'auditorium' in lower:
        return 'SEMINAR_HALL'
    return None  # No suggestion — user must select
```

When creating resources from UI:
```python
# Authorized resource creation flow
@router.post("/resources", response_model=ResourceOut)
def create_resource(
    payload: ResourceCreatePayload,  # Contains explicit type from user
    db: Session = Depends(get_db)
):
    # User provides explicit type — not inferred
    resource = Resource(
        name=payload.name,
        normalized_name=normalize_resource_name(payload.name),
        resource_type=payload.resource_type,  # USER-PROVIDED
        department=payload.department,
        capacity=payload.capacity
    )
    db.add(resource)
    db.commit()
    return resource
```

### 7.5 Database Migration Strategy (Cautious)

**Current State**: `schedule_entries.room` is nullable TEXT

**Phase 2A** (Non-Breaking):
```sql
-- Step 1: Add nullable resource_id column
ALTER TABLE schedule_entries
    ADD COLUMN resource_id UUID REFERENCES resources(id);

-- Keep room TEXT column for now
```

**Phase 2B** (After Safe Backfill):
```sql
-- Step 2: Backfill known resources
-- Run migration script that:
--   1. Extracts unique room texts
--   2. Attempts resolution
--   3. Creates Resource records for RESOLVED cases
--   4. Updates schedule_entries.resource_id
--   5. Logs UNRESOLVED cases for manual review

-- Step 3: After manual review complete
ALTER TABLE schedule_entries
    ALTER COLUMN resource_id SET NOT NULL;

-- Step 4: (Later, breaking change)
ALTER TABLE schedule_entries
    DROP COLUMN room;
```

**Migration Script**:
```python
# database/migrations/backfill_resources.py

def backfill_resources(db: Session):
    """
    Cautious resource backfill.
    Does NOT auto-create — only resolves known resources.
    """
    entries = db.scalars(
        select(ScheduleEntry)
        .join(Timetable).join(Teacher)
        .where(ScheduleEntry.room.isnot(None), ScheduleEntry.room != '')
    ).all()
    
    resolver = ResourceResolver(db)
    unresolved_log = []
    
    for entry in entries:
        dept = entry.timetable.teacher.department
        resource_id, status = resolver.resolve(entry.room, dept)
        
        if status == "RESOLVED":
            entry.resource_id = resource_id
        else:
            unresolved_log.append({
                "room": entry.room,
                "department": dept,
                "status": status,
                "entry_id": str(entry.id)
            })
    
    # Log unresolved for manual review
    with open("unresolved_resources.json", "w") as f:
        json.dump(unresolved_log, f, indent=2)
    
    db.commit()
    print(f"Backfilled {len(entries) - len(unresolved_log)} resources")
    print(f"Unresolved: {len(unresolved_log)} (see unresolved_resources.json)")
```

---


## 8. TEACHER IDENTITY ARCHITECTURE

### 8.1 Department-Scoped Identity

**Current Problem**: Globally unique `uq_teachers_acronym_normalized` prevents:
```
Computer Applications: DNS → Dr. Alice
Commerce: DNS → Dr. Bob
→ Second insert fails with 409 CONFLICT
```

**Phase 2 Solution**: Department-scoped uniqueness:
```sql
-- Drop global unique constraint
DROP INDEX IF EXISTS uq_teachers_acronym_normalized;

-- Create department-scoped constraint
CREATE UNIQUE INDEX uq_teachers_acronym_dept_normalized
    ON teachers (lower(btrim(acronym)), lower(btrim(department)));

-- Allow cross-department search
CREATE INDEX idx_teachers_acronym_all_depts
    ON teachers (lower(btrim(acronym)));
```

### 8.2 Teacher Resolution

```python
# backend/app/services/teacher_resolver.py (NEW)

class TeacherResolver:
    def __init__(self, db: Session):
        self.db = db
    
    def resolve(
        self, acronym: str, department: str
    ) -> tuple[UUID | None, Literal["REUSE", "CREATE", "CONFLICT"]]:
        """
        Resolve teacher by (acronym, department).
        
        REUSE: Exact match found → reuse existing teacher
        CREATE: No match → create new teacher
        CONFLICT: Multiple matches or name mismatch → manual resolution required
        """
        normalized_acronym = acronym.strip().upper()
        normalized_dept = department.strip()
        
        matches = db.scalars(
            select(Teacher).where(
                func.lower(func.btrim(Teacher.acronym)) == normalized_acronym.lower(),
                func.lower(func.btrim(Teacher.department)) == normalized_dept.lower(),
                Teacher.is_active.is_(True)
            )
        ).all()
        
        if len(matches) == 0:
            return None, "CREATE"
        elif len(matches) == 1:
            return matches[0].id, "REUSE"
        else:
            return None, "CONFLICT"  # Should not happen with unique constraint, but defensive
```

### 8.3 API Changes

```python
# backend/app/api/teachers.py (modified)

@router.get("/search", response_model=list[TeacherOut])
def search_teachers(
    q: str = Query(min_length=1),
    department: str | None = Query(default=None),  # NEW: optional filter
    db: Session = Depends(get_db)
) -> list[Teacher]:
    """
    Search teachers by name or acronym.
    If department provided: filter to that department.
    If department omitted: search all departments.
    """
    term = q.strip()
    pattern = f"%{term}%"
    
    stmt = select(Teacher).where(
        Teacher.is_active.is_(True),
        Teacher.name.ilike(pattern) | Teacher.acronym.ilike(pattern)
    )
    
    if department:
        stmt = stmt.where(
            func.lower(Teacher.department) == department.lower()
        )
    
    return list(db.scalars(stmt.order_by(Teacher.department, Teacher.name).limit(25)).all())
```

### 8.4 UI Department Filter

```typescript
// frontend/src/pages/DirectorPage.tsx (modified)

function DirectorSearch() {
  const [query, setQuery] = useState('');
  const [department, setDepartment] = useState<string>('');  // NEW
  
  const { data: teachers } = useQuery({
    queryKey: ['teachers', 'search', query, department],
    queryFn: () => searchTeachers(query, department),  // Pass department
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
        {/* Load departments dynamically if needed */}
      </select>
      
      {/* Results show department: "DNS (Computer Applications)" */}
    </>
  );
}
```

---

## 9. AVAILABILITY ARCHITECTURE

### 9.1 Teacher Availability (Enhanced)

**Requirements**:
- Remove "FREE" text from empty slots
- Show occupied slots with type, subject, section, room
- Multi-slot activities as ONE merged visual block
- Do NOT use "↑ continued" markers
- 12-hour AM/PM time format

**Current UI Issues**:
```tsx
// WRONG (current implementation):
<div className="avail-status avail-status--free">
  <span className="avail-free-badge">FREE</span>  {/* REMOVE THIS */}
</div>
```

**Correct UI**:
```tsx
// Occupied slot
<div className="avail-row avail-row--occupied">
  <span className="avail-time">8:00 AM – 8:55 AM</span>
  <span className="avail-code">S1</span>
  <div className="avail-status">
    <span className="entry-type entry-type--class">CLASS</span>
    <span className="subject">Database Management Systems</span>
    <span className="meta">MCA-A · Room 204</span>
  </div>
</div>

// Free slot (NO TEXT, just styling)
<div className="avail-row avail-row--free">
  <span className="avail-time">8:55 AM – 9:50 AM</span>
  <span className="avail-code">S2</span>
  <div className="avail-status avail-status--free">
    {/* Empty — styling shows it's available */}
  </div>
</div>
```

```css
/* Styling conveys status, not text */
.avail-row--free {
  background: #f9fafb;
  border-left: 3px solid #10b981;  /* Green = available */
}

.avail-row--occupied {
  background: white;
  border-left: 3px solid #3b82f6;  /* Blue = occupied */
}
```

### 9.2 Resource Availability (NEW)

```python
# backend/app/api/resources.py (NEW)

@router.get("/{resource_id}/timetable", response_model=ResourceDayOut)
def get_resource_timetable_day(
    resource_id: UUID,
    day: str = Query(description="Lowercase day name"),
    academic_year: str = Query(description="e.g. 2025-2026"),
    db: Session = Depends(get_db)
) -> ResourceDayOut:
    """
    Return resource occupancy for a day.
    Shows which teachers/activities are using this resource.
    """
    day_iso = DAY_NAME_TO_ISO.get(day.lower())
    if not day_iso:
        raise HTTPException(400, f"Invalid day: {day}")
    
    # Verify resource exists
    resource = db.scalar(select(Resource).where(Resource.id == resource_id))
    if not resource:
        raise HTTPException(404, "Resource not found")
    
    # Find all CONFIRMED activities using this resource on this day
    entries = db.scalars(
        select(ScheduleEntry)
        .join(Timetable)
        .join(Teacher)
        .options(
            selectinload(ScheduleEntry.timetable).selectinload(Timetable.teacher),
            selectinload(ScheduleEntry.slot_links).selectinload(ScheduleEntrySlot.time_slot)
        )
        .where(
            Timetable.academic_year == academic_year,
            Timetable.status == "CONFIRMED",
            ScheduleEntry.resource_id == resource_id,
            ScheduleEntry.day_of_week == day_iso
        )
    ).all()
    
    # Build slot_code → activity map
    uuid_to_code = {ts.id: ts.code for ts in db.scalars(select(TimeSlot)).all()}
    slot_to_activity: dict[str, ResourceActivityOut] = {}
    
    for entry in entries:
        codes = [uuid_to_code[link.time_slot_id] for link in entry.slot_links]
        activity = ResourceActivityOut(
            id=entry.id,
            teacher_id=entry.timetable.teacher_id,
            teacher_name=entry.timetable.teacher.name,
            teacher_acronym=entry.timetable.teacher.acronym,
            entry_type=entry.entry_type,
            subject_or_activity=entry.subject_or_activity,
            section=entry.section,
            slot_codes=sorted(codes)
        )
        for code in codes:
            slot_to_activity[code] = activity
    
    # Build periods grid
    periods = []
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
        day=day,
        periods=periods
    )
```

**Response Schema**:
```python
class ResourceActivityOut(BaseModel):
    """Activity occupying a resource slot"""
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
    code: str | None
    label: str | None
    start_time: str
    end_time: str
    activity: ResourceActivityOut | None

class ResourceDayOut(BaseModel):
    resource_id: UUID
    resource_name: str
    academic_year: str
    day: str
    periods: list[ResourcePeriodOut]
```

---


## 10. DIRECT EDITING ARCHITECTURE

### 10.1 Editor → Canonical → Validation Flow

**Critical Principle**: Direct editing MUST use the SAME canonical model and validation engine.

```
Editor UI change
  ↓
Convert to CanonicalTimetable
  ↓
ValidationEngine.validate()
  ↓
If no blocking errors: persist as DRAFT
  ↓
Return validation results to UI
```

**NOT this** (WRONG):
```
Editor payload → different validation rules → save
```

### 10.2 Editor Integration

```python
# backend/app/api/teachers.py (enhanced)

@router.put("/{teacher_id}/timetable", response_model=TimetableWriteOut)
def update_timetable(
    teacher_id: UUID,
    payload: TimetableWriteIn,
    db: Session = Depends(get_db)
) -> TimetableWriteOut:
    """
    Update DRAFT timetable.
    Uses SAME validation as imports.
    """
    teacher = _get_teacher(db, teacher_id)
    
    # Convert editor payload → CanonicalTimetable
    canonical = editor_payload_to_canonical(payload, teacher_id, db)
    
    # Validate using SAME engine as imports
    engine = ValidationEngine(db)
    issues = engine.validate(canonical)
    
    # Block on errors
    errors = [i for i in issues if i.severity == "ERROR"]
    if errors:
        raise HTTPException(422, detail={
            "message": "Validation errors prevent save",
            "issues": [i.dict() for i in errors]
        })
    
    # Find existing DRAFT
    draft = db.scalar(
        select(Timetable).where(
            Timetable.teacher_id == teacher_id,
            Timetable.academic_year == payload.academic_year,
            Timetable.status == "DRAFT"
        )
    )
    
    if not draft:
        raise HTTPException(404, "No DRAFT timetable found (use POST to create)")
    
    # Atomically replace (same as current implementation)
    existing_entries = db.scalars(
        select(ScheduleEntry).where(ScheduleEntry.timetable_id == draft.id)
    ).all()
    for entry in existing_entries:
        db.delete(entry)
    db.flush()
    
    # Insert new entries from canonical
    for activity in canonical.activities:
        # ... (same persistence logic as imports)
        ...
    
    db.commit()
    return _build_write_out(draft, db)
```

### 10.3 Editor UI Components

```tsx
// frontend/src/components/TimetableEditor.tsx (NEW)

export function TimetableEditor({ teacherId, academicYear }: Props) {
  const [days, setDays] = useState<TimetableWritePayload['days']>({});
  const [selectedDay, setSelectedDay] = useState<DayName>('monday');
  
  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = { academic_year: academicYear, days };
      return updateTimetable(teacherId, payload);
    },
    onError: (error) => {
      // Show validation errors from API
      if (error.response?.data?.issues) {
        setValidationIssues(error.response.data.issues);
      }
    }
  });
  
  const handleAddActivity = (slot: string) => {
    // Open dialog to add activity at slot
    ...
  };
  
  const handleEditActivity = (activityIndex: number) => {
    // Open dialog to edit activity
    ...
  };
  
  const handleDeleteActivity = (activityIndex: number) => {
    const dayEntries = [...(days[selectedDay] ?? [])];
    dayEntries.splice(activityIndex, 1);
    setDays({ ...days, [selectedDay]: dayEntries });
  };
  
  const handleMoveActivity = (fromDay: DayName, toDay: DayName, index: number) => {
    const from = [...(days[fromDay] ?? [])];
    const [moved] = from.splice(index, 1);
    const to = [...(days[toDay] ?? []), moved];
    setDays({ ...days, [fromDay]: from, [toDay]: to });
  };
  
  // Client-side validation (OPTIONAL UX feedback only)
  // ADVISORY ONLY — backend ValidationEngine is authoritative
  const clientValidate = (activity: ScheduleEntryPayload): string[] => {
    const errors: string[] = [];
    
    // LAB must be 2 consecutive slots
    if (activity.entry_type === 'LAB' && !isValidLabPair(activity.slot_ids)) {
      errors.push('LAB must occupy exactly 2 consecutive slots');
    }
    
    // No duplicate slots
    const allSlots = (days[selectedDay] ?? []).flatMap(e => e.slot_ids);
    const duplicates = activity.slot_ids.filter(s => allSlots.includes(s));
    if (duplicates.length > 0) {
      errors.push(`Slot(s) ${duplicates.join(', ')} already occupied`);
    }
    
    return errors;
  };
  
  return (
    <div className="timetable-editor">
      {/* Day tabs */}
      {/* Grid view with add/edit/delete/move controls */}
      {/* Save button → triggers saveMutation */}
      {/* Validation errors display */}
    </div>
  );
}
```

**Client-Side Validation Principles**:

1. **Advisory Only**: Client-side validation provides instant UX feedback, but is NOT authoritative
2. **Backend is Truth**: Every create/edit/move/delete/publish MUST be server-validated
3. **No Business Rule Duplication**: Do NOT duplicate validation rules as independent business logic in frontend
4. **Simple Checks Only**: Client-side may check format (e.g., LAB needs 2 slots), but teacher overlap, resource conflicts, etc. are backend-only
5. **Always Defer to Server**: If client says "valid" but server returns 422, server wins

### 10.4 Concurrency Control

**Problem**: Two users edit same timetable concurrently.

**Solution**: Optimistic locking with `updated_at` timestamp.

```python
# backend/app/models/models.py (already has updated_at)

class Timetable(Base):
    # ... existing fields ...
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
```

```python
# backend/app/api/teachers.py (add version check)

@router.put("/{teacher_id}/timetable")
def update_timetable(
    teacher_id: UUID,
    payload: TimetableWriteIn,
    if_unmodified_since: datetime | None = Header(default=None),  # NEW
    db: Session = Depends(get_db)
):
    draft = ...  # find DRAFT
    
    # Check version
    if if_unmodified_since and draft.updated_at > if_unmodified_since:
        raise HTTPException(409, detail={
            "message": "Timetable was modified by another user",
            "current_version": draft.updated_at.isoformat()
        })
    
    # Proceed with update
    ...
```

```typescript
// frontend: include If-Unmodified-Since header

export async function updateTimetable(
  teacherId: string,
  payload: TimetableWritePayload,
  lastKnownVersion?: string  // ISO timestamp
): Promise<TimetableWriteResponse> {
  const headers: any = {};
  if (lastKnownVersion) {
    headers['If-Unmodified-Since'] = lastKnownVersion;
  }
  
  const response = await api.put(
    `/teachers/${teacherId}/timetable`,
    payload,
    { headers }
  );
  return response.data;
}
```

---

## 11. BULK REVIEW/PUBLISH ARCHITECTURE

### 11.1 Bulk Confirm Endpoint

```python
# backend/app/api/imports.py (NEW endpoint)

@router.post(
    "/{import_id}/confirm-bulk",
    response_model=BulkConfirmOut,
    summary="Confirm import: validate all, publish atomically"
)
def confirm_bulk(
    import_id: str,
    publish: bool = Query(default=False, description="If true, promote DRAFTs → CONFIRMED"),
    db: Session = Depends(get_db)
) -> BulkConfirmOut:
    """
    Bulk confirmation with atomic publishing.
    
    Phase 1 (publish=False):
      - Validate entire canonical timetable
      - If errors: return 422 with issues
      - Persist as DRAFT for all teachers
      - Return summary
    
    Phase 2 (publish=True):
      - Validate entire canonical timetable
      - If errors: return 422 with issues
      - Persist as DRAFT
      - Promote all DRAFTs → CONFIRMED in ONE transaction
      - Return summary
    
    ATOMICITY: All teachers published or none (no partial success).
    
    CRITICAL: Staging stores CanonicalTimetable (or serialized canonical),
    NOT ImportPreview. ImportPreview is derived from canonical for API response.
    """
    # Retrieve canonical timetable from staging
    # _STAGING: dict[str, CanonicalTimetable] (NOT ImportPreview)
    canonical = _STAGING.get(import_id)
    if not canonical:
        raise HTTPException(404, "Import not found")
    
    # Validate
    engine = ValidationEngine(db)
    issues = engine.validate(canonical)
    
    # Block on errors
    if engine.has_blocking_errors(issues):
        raise HTTPException(422, detail={
            "message": "Validation errors prevent confirmation",
            "issues": [i.dict() for i in issues]
        })
    
    # Persist as DRAFT
    result = persist_import(canonical, db, source="IMPORT")
    
    if publish:
        # Promote all to CONFIRMED
        timetable_ids = result["timetables_created"] + result["timetables_replaced"]
        for tt_id_str in timetable_ids:
            tt = db.scalar(select(Timetable).where(Timetable.id == UUID(tt_id_str)))
            if tt and tt.status == "DRAFT":
                # Run per-timetable structural validation (same as single-teacher confirm)
                validate_timetable_structure(tt, db)
                tt.status = "CONFIRMED"
                tt.last_verified_at = datetime.now(timezone.utc)
    
    try:
        db.commit()  # Atomic: all or nothing
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, "Bulk confirmation failed, all changes rolled back") from exc
    
    _STAGING.pop(import_id, None)
    
    return BulkConfirmOut(
        import_id=import_id,
        academic_year=canonical.academic_year,
        teachers_created=result["teachers_created"],
        teachers_reused=result["teachers_reused"],
        timetables_confirmed=timetable_ids if publish else [],
        timetables_draft=timetable_ids if not publish else []
    )
```

### 11.2 Inline Editing in Preview

```tsx
// frontend/src/pages/ExcelImportPage.tsx (enhanced)

function ImportPreviewEditor({ preview }: { preview: ImportPreview }) {
  const [validationIssues, setValidationIssues] = useState(preview.validation_issues);
  
  const errors = validationIssues.filter(i => i.severity === 'ERROR');
  const warnings = validationIssues.filter(i => i.severity === 'WARNING');
  
  return (
    <div className="import-preview">
      {/* Validation Issues Panel */}
      <div className="validation-panel">
        {errors.length > 0 && (
          <div className="issue-group issue-group--error">
            <h4>{errors.length} Errors (must fix before confirming)</h4>
            {errors.map((issue, i) => (
              <ValidationIssueCard key={i} issue={issue} />
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
      
      {/* Editable Teachers Table */}
      {/* Editable Schedule Preview */}
      
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

function ValidationIssueCard({ issue }: { issue: ValidationIssue }) {
  return (
    <div className={`issue-card issue-card--${issue.severity.toLowerCase()}`}>
      <div className="issue-header">
        <span className="issue-code">{issue.code}</span>
        <span className="issue-severity-badge">{issue.severity}</span>
      </div>
      <p className="issue-message">{issue.message}</p>
      {issue.source_locations && issue.source_locations.length > 0 && (
        <p className="issue-source">
          Source: {issue.source_locations.map(loc => loc.display_ref).join(', ')}
        </p>
      )}
      {issue.suggestion && (
        <p className="issue-suggestion">💡 {issue.suggestion}</p>
      )}
    </div>
  );
}
```

---


## 12. ERROR MODEL & SOURCE TRACEABILITY

### 12.1 Parse Error vs Validation Error

**Parse Error** (Cannot produce canonical timetable):
```python
class ParseError(Exception):
    """Source structure cannot be understood"""
    pass

# Example:
raise ParseError(
    "DOCX Table 2 has broken vMerge chain at row 8. "
    "Fix cell merging in Word and re-upload."
)
```

**Validation Error** (Canonical timetable produced, but semantically invalid):
```python
ValidationIssue(
    severity="ERROR",
    code="TEACHER_OVERLAP",
    message="Teacher DNS has overlapping activities on Thursday at S2",
    source_locations=(
        SourceLocation(source_type="DOCX", table_index=2, table_row=15),
        SourceLocation(source_type="DOCX", table_index=2, table_row=18)
    ),
    suggestion="Reschedule one of the conflicting activities"
)
```

### 12.2 Source Location Examples

**DOCX**:
```python
SourceLocation(
    source_type="DOCX",
    table_index=2,      # Third table in document
    table_row=5,        # Sixth row in that table
    table_col=3         # Fourth column
).display_ref()
# → "Table 2, Row 5, Col 3"
```

**XLSX**:
```python
SourceLocation(
    source_type="XLSX",
    sheet_name="Schedule",
    row_number=12,
    field_name="teacher_acronym"
).display_ref()
# → "Schedule!12 (teacher_acronym)"
```

**Manual Edit**:
```python
SourceLocation(
    source_type="MANUAL"
).display_ref()
# → "Manual Edit"
```

### 12.3 Validation Issue Structure

```python
@dataclass(frozen=True)
class ValidationIssue:
    severity: Literal["ERROR", "WARNING", "INFO"]
    code: str
    message: str
    source_locations: tuple[SourceLocation, ...]
    affected_entities: dict[str, Any]  # {"teacher": "DNS", "day": "thursday", "slot": "S2"}
    suggestion: str | None
    
    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "source_locations": [loc.to_dict() for loc in self.source_locations],
            "affected_entities": self.affected_entities,
            "suggestion": self.suggestion
        }
```

---

## 13. API EVOLUTION

### 13.1 No API Versioning Change

**Keep `/api/v1`** for Phase 2.

Phase 2 adds new endpoints but does NOT introduce breaking changes to existing contracts.

### 13.2 New Endpoints

```
POST   /api/v1/imports/docx                    Upload DOCX (same response as /excel)
POST   /api/v1/imports/{id}/confirm-bulk       Bulk confirm with optional publish
GET    /api/v1/resources                       List resources
GET    /api/v1/resources/search                Search resources
GET    /api/v1/resources/{id}                  Get resource details
GET    /api/v1/resources/{id}/timetable        Get resource availability (day view)
```

### 13.3 Enhanced Endpoints

```
GET    /api/v1/teachers/search?q=&department=  Add optional department filter (backward compat)
POST   /api/v1/imports/excel                   Enhanced validation response (additive)
```

### 13.4 Response Schema Evolution

**ImportPreview** (additive changes only):
```typescript
// OLD (Phase 1)
interface ImportPreview {
  import_id: string;
  academic_year: string;
  teachers: TeacherImportRow[];
  days: Record<string, ScheduleImportRow[]>;
  warnings: string[];
  errors: string[];
}

// NEW (Phase 2 — backward compatible)
interface ImportPreview {
  import_id: string;
  academic_year: string;
  department?: string;  // NEW: optional
  teachers: TeacherImportRow[];
  days: Record<string, ScheduleImportRow[]>;
  validation_issues?: ValidationIssue[];  // NEW: structured issues
  warnings: string[];  // DEPRECATED but kept for backward compat
  errors: string[];    // DEPRECATED but kept for backward compat
}
```

Frontend can check:
```typescript
const issues = preview.validation_issues ?? [];
const legacyErrors = preview.errors ?? [];
// Use issues if available, fallback to legacyErrors
```

---

## 14. IMPLEMENTATION ROADMAP

### 14.1 Solo Implementation Sequence

**Phase 2 Foundation** (Weeks 1-2):
1. Define `CanonicalTimetable` domain model (`backend/app/domain/timetable.py`)
2. Define `ValidationEngine` interface (`backend/app/domain/validation.py`)
3. Create `SourceLocation`, `ValidationIssue` data classes
4. Refactor XLSX adapter to produce `CanonicalTimetable`
5. Refactor existing validator to operate on `CanonicalTimetable`
6. Test: existing XLSX imports still work

**DOCX Import** (Weeks 3-4):
7. Create `backend/app/services/docx_import/` module structure
8. Implement table extraction (`parser.py`)
9. Implement geometry reconstruction (`geometry.py`)
10. Implement row classification (`classifier.py`)
11. Implement field extraction (`extractor.py`)
12. Implement DOCX → `CanonicalTimetable` adapter
13. Test: upload sample DOCX → preview works
14. Handle known ambiguities (Tuesday I-A S4+S5) → returns ERROR

**Validation Engine** (Weeks 5-6):
15. Implement teacher overlap detection
16. Implement resource overlap detection
17. Implement LAB duration/consecutive validation
18. Implement break/lunch crossing detection
19. Implement all rules from section 5.2
20. Test: 50+ edge cases from validation matrix

**Resource Management** (Weeks 7-8):
21. Create `resources` table schema
22. Create `resource_aliases` table
23. Add nullable `resource_id` to `schedule_entries`
24. Implement `ResourceResolver` class
25. Create `/api/v1/resources/*` endpoints
26. Write migration script for backfilling (cautious, no auto-create)
27. Test: resource resolution, alias matching

**Teacher Identity Migration** (Week 9):
28. Drop `uq_teachers_acronym_normalized` constraint
29. Create `uq_teachers_acronym_dept_normalized` constraint
30. Update `TeacherResolver` for department scope
31. Update `/teachers/search` with department filter
32. Test: create cross-department teachers with same acronym

**Direct Editing** (Weeks 10-11):
33. Create editor → `CanonicalTimetable` adapter
34. Wire `PUT /teachers/{id}/timetable` to use `ValidationEngine`
35. Implement optimistic locking (version check)
36. Build `TimetableEditor` UI component
37. Test: add/edit/delete activities, validation blocks invalid saves

**Bulk Operations** (Weeks 12-13):
38. Implement `POST /imports/{id}/confirm-bulk`
39. Implement atomic publishing logic
40. Build inline editing in import preview UI
41. Build `ValidationIssueCard` component
42. Test: bulk import 100 teachers → publish atomically

**Availability UI Polish** (Week 14):
43. Remove "FREE" badges from `DirectorTeacherPage.tsx`
44. Update CSS for style-based availability
45. Verify 12-hour AM/PM formatting
46. Build `DirectorResourcePage.tsx` (resource availability)
47. Test: search Lab1A → view availability → shows occupying teachers

**Integration & Testing** (Weeks 15-16):
48. End-to-end: DOCX import → validation → fix → bulk publish
49. End-to-end: Direct editing → validation → save DRAFT → publish
50. Performance testing: 100 teachers, 500+ activities
51. Edge case matrix: test all scenarios from section 15
52. Documentation updates
53. Bug fixes & polish

**Total Estimated Timeline**: 16 weeks (4 months) for solo implementation

### 14.2 Critical Dependencies

| Phase | Blocks | Reason |
|-------|--------|--------|
| Foundation → DOCX | DOCX needs `CanonicalTimetable` | Canonical model must exist |
| Foundation → Validation | Validation operates on canonical | Engine interface must exist |
| Validation → Editing | Editor uses same validation | Single engine principle |
| Resources → Availability | Resource API needed for UI | Backend must exist first |
| All Backend → UI | Frontend depends on APIs | API contracts must be stable |

---


## 15. COMPREHENSIVE EDGE-CASE MATRIX

### 15.1 File Validation

| Case | Detection | Severity | Message | Recovery |
|------|-----------|----------|---------|----------|
| Wrong extension (.txt, .pdf) | Upload guard | ERROR | "Only .xlsx and .docx accepted" | Re-upload correct file |
| Corrupt XLSX (truncated) | Parser exception | PARSE ERROR | "File is corrupt or incomplete" | Re-export from Excel |
| Corrupt DOCX (invalid XML) | Parser exception | PARSE ERROR | "File is corrupt or malformed" | Re-save in Word |
| Empty file (0 bytes) | Upload guard | ERROR | "Uploaded file is empty" | Upload valid file |
| XLSX with no sheets | Parser | PARSE ERROR | "Workbook contains no sheets" | Add required sheets |
| XLSX missing Teachers sheet | Parser | PARSE ERROR | "Required 'Teachers' sheet not found" | Add Teachers sheet |
| XLSX missing Schedule sheet | Parser | PARSE ERROR | "Required 'Schedule' sheet not found" | Add Schedule sheet |
| DOCX with no tables | Parser | PARSE ERROR | "No tables found in document" | Add timetable table |

### 15.2 Teacher Validation

| Case | Detection | Severity | Blocks? |
|------|-----------|----------|---------|
| Missing name | Normalizer | ERROR | Yes |
| Missing acronym | Normalizer | ERROR | Yes |
| Duplicate acronym (same dept) | Validator | CONFLICT | Yes |
| Duplicate acronym (diff dept) | Validator | INFO | No |
| Teacher has no activities | Validator | INFO | No |
| Invalid level (not UG/PG) | Normalizer | ERROR | Yes |
| Invalid semester (≤0) | Normalizer | ERROR | Yes |
| Unknown program | Validator | ERROR | Yes |
| **Teacher overlap (same slot)** | Validator | ERROR | Yes |

### 15.3 Resource Validation

| Case | Detection | Severity | Blocks? |
|------|-----------|----------|---------|
| **Resource unresolved** | Validator | ERROR | Yes |
| **Resource ambiguous (multiple matches)** | Validator | ERROR | Yes |
| **Resource overlap (same slot)** | Validator | ERROR | Yes |
| Resource inactive | Validator | WARNING | No |
| Blank resource field | Normalizer | INFO | No |

### 15.4 Schedule Validation

| Case | Detection | Severity | Blocks? |
|------|-----------|----------|---------|
| Sunday schedule | Normalizer | ERROR | Yes |
| Invalid slot code | Normalizer | ERROR | Yes |
| LAB not exactly 2 slots | Validator | ERROR | Yes |
| LAB non-consecutive | Validator | ERROR | Yes |
| LAB crosses break | Validator | ERROR | Yes |
| LAB crosses lunch | Validator | ERROR | Yes |
| 3+ consecutive slots | Validator | ERROR | Yes |
| Slot occupied twice (same teacher/day) | Validator | ERROR | Yes |
| Break/lunch used as working slot | Validator | ERROR | Yes |

### 15.5 DOCX-Specific Cases

| Case | Detection | Severity | Message |
|------|-----------|----------|---------|
| Broken vMerge chain | Geometry reconstructor | PARSE ERROR | "Table X vertical merge broken at row Y" |
| gridSpan exceeds table width | Geometry reconstructor | PARSE ERROR | "Table X horizontal span invalid at row Y" |
| No clear day column | Classifier | PARSE ERROR | "Cannot identify day column in table" |
| **Ambiguous teacher→activity mapping** | Extractor | ERROR | "Multiple teachers for one activity, cannot determine assignment" |
| **Ambiguous room→activity mapping** | Extractor | ERROR | "Multiple activities, insufficient room labels" |
| Multiple table formats | Parser | WARNING | "Multiple formats detected, using first compatible" |

### 15.6 Editing Cases

| Case | Detection | Severity | Recovery |
|------|-----------|----------|----------|
| Edit creates teacher overlap | Validator | ERROR | Choose different slot |
| Edit creates resource overlap | Validator | ERROR | Choose different resource/slot |
| Moving activity to occupied slot | Client validation | ERROR | Choose free slot |
| Concurrent edit (stale version) | Optimistic lock | 409 CONFLICT | Reload and retry |
| Saving with validation errors | Client validation | ERROR | Fix errors first |

### 15.7 Availability Cases

| Case | System Behavior |
|------|-----------------|
| No CONFIRMED timetable | Show "No confirmed data" message, all slots unknown |
| CONFIRMED with no entries | Show all 9 slots as available (no text, just styling) |
| Teacher search returns none | "No teachers found matching '{query}'" |
| Resource search returns none | "No resources found matching '{query}'" |
| Activity with unresolved resource | Show activity with "Room: Unknown" |

---

## 16. TESTING STRATEGY

### 16.1 Domain Model Tests (30 tests)

**CanonicalTimetable**:
- Immutability (frozen dataclasses)
- Source location display refs
- Teacher identity equality
- Resource reference resolution status

**Validation Engine**:
- Each validation rule independently
- Teacher overlap detection (various scenarios)
- Resource overlap detection
- LAB duration/consecutive validation
- Break/lunch crossing
- Department-scoped resolution

### 16.2 DOCX Parser Tests (40 tests)

- Simple table (no merges)
- Vertical merge (teacher spans rows)
- Horizontal merge (section spans columns)
- LAB spanning two time rows
- Multiple teachers in one table
- Ambiguous layouts → PARSE ERROR
- Tuesday I-A S4+S5 scenario → ERROR
- Invalid time strings
- Missing required fields
- Broken vMerge chain

### 16.3 Integration Tests (25 tests)

- XLSX import → canonical → validation → persist
- DOCX import → canonical → validation → persist
- Import with errors → validation issues returned
- Bulk confirm (publish=false) → all DRAFT
- Bulk confirm (publish=true) → all CONFIRMED, atomic
- Direct edit → validation → save DRAFT
- Concurrent edit → 409 CONFLICT
- Resource resolution → RESOLVED/UNRESOLVED/AMBIGUOUS
- Teacher overlap → ERROR blocks publish
- Resource overlap → ERROR blocks publish

### 16.4 End-to-End Tests (8 critical paths)

1. **DOCX Import Flow**: Upload → Preview → Fix errors → Bulk publish → Verify CONFIRMED
2. **XLSX Import Flow**: Upload → Preview → Bulk publish → Verify CONFIRMED
3. **Direct Editing**: Open editor → Add activity → Save → Reload → Verify
4. **Validation Blocking**: Upload with overlap → Attempt publish → Blocked
5. **Resource Availability**: Search Lab1A → View daily → Shows occupying teachers
6. **Teacher Availability**: Search DNS → View weekly → No "FREE" text
7. **Cross-Department Teachers**: Create DNS (Comp Apps) + DNS (Commerce) → Both succeed
8. **Ambiguous DOCX**: Upload Tuesday I-A file → ERROR with specific location

### 16.5 Performance Tests

- Import 100 teachers (large DOCX): <10s parse
- Validation 500+ activities: <5s
- Bulk publish 100 teachers: <30s (single transaction)
- Resource availability query: <500ms
- Concurrent edits (10 users): no data corruption

---

## 17. SUCCESS METRICS (CORRECTED)

### 17.1 Correctness Metrics (NOT "success rate")

| Metric | Target | Measurement |
|--------|--------|-------------|
| Supported DOCX templates mapped 100% correctly | 100% | Zero incorrect mappings in test suite |
| Known source ambiguities flagged 100% | 100% | Tuesday I-A case returns ERROR |
| Zero silent guessing | 100% | Code review: no auto-create without explicit approval |
| Zero validation rule violations in CONFIRMED | 100% | No teacher/resource overlaps published |
| Validation engine catches all test cases | 100% | All edge-case matrix scenarios detected |

### 17.2 User Experience Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Import-to-publish time | <5 minutes | User timer: upload → fixed errors → published |
| Error message clarity | >4.0/5 | Post-release survey: "errors were understandable" |
| Error recovery success | >90% | % of users who fix errors and succeed |
| Direct editing adoption | >50% | % of timetables edited manually post-import |

### 17.3 Performance Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| DOCX parse (100 teachers) | <10s | Backend timer |
| Validation engine (500 activities) | <5s | ValidationEngine.validate() duration |
| Bulk publish (100 teachers) | <30s | Transaction commit time |
| Resource availability query | <500ms | API response time |

---

## 18. RISKS & MITIGATION

### 18.1 Technical Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| DOCX geometry reconstruction fails for complex layouts | Medium | High | Extensive unit tests, document supported layouts, return PARSE ERROR for unsupported |
| Known ambiguity (Tuesday I-A) cannot be auto-resolved | High (expected) | Medium | Architecture principle: flag ERROR, require manual resolution |
| Resource backfill migration creates incorrect mappings | Medium | High | Cautious migration: resolve only known resources, log unresolved, manual review required |
| Bulk publish transaction timeout (100+ teachers) | Low | Medium | Test at scale, optimize if needed, keep atomic semantics |
| Concurrent editing causes lost updates | Medium | Medium | Optimistic locking with version check (409 CONFLICT) |

### 18.2 Data Quality Risks

| Risk | Mitigation |
|------|------------|
| Existing CONFIRMED timetables have teacher overlaps | Run ValidationEngine on existing data before Phase 2 deploy, fix proactively |
| Room name variations create duplicate resources | Normalization + alias system, admin UI to merge duplicates |
| Users publish invalid data via bulk operation | Block on ERROR (atomicity), show error count, require fix before publish |

### 18.3 Implementation Risks

| Risk | Mitigation |
|------|------------|
| Solo implementation takes longer than 16 weeks | Prioritize critical path: Foundation → XLSX refactor → Validation → DOCX, defer UI polish |
| Scope creep (PDF import, OCR, etc.) | Document Phase 3 separately, stay focused on Phase 2 requirements |
| Migration causes downtime | Test migration on replica, blue-green deployment, rollback plan |

---

## 19. OPEN DECISIONS REQUIRING APPROVAL

### 19.1 DOCX Table Format

**Question**: Should we support multiple DOCX table layouts or define ONE canonical format?

**Options**:
A. Define ONE canonical format, document it, reject others with PARSE ERROR
B. Support multiple layouts with auto-detection

**Recommendation**: Option A (Phase 2), Option B (Phase 3 if needed)

### 19.2 Resource Auto-Creation

**Question**: Should authorized users be able to create resources during import review?

**Options**:
A. Users must pre-create resources in admin UI before import
B. Import preview shows "Create Resource" button for unresolved names
C. Bulk import includes resource creation workflow

**Recommendation**: Option B (inline creation during review, explicit approval required)

### 19.3 Validation Severity Levels

**Question**: Should we allow publish with WARNINGs?

**Options**:
A. Block on ERROR only, allow WARNING (current spec)
B. Block on ERROR + WARNING, require explicit override
C. Configurable per-rule severity

**Recommendation**: Option A (allow WARNING), user can review and proceed

### 19.4 Department Entity

**Question**: Should we normalize `department` into a `departments` table?

**Options**:
A. Keep as free text (current)
B. Introduce `departments` table with department_id FK

**Recommendation**: Option A for Phase 2 (less migration risk), Option B for Phase 3

### 19.5 Chunked Publishing

**Question**: If bulk publish >100 teachers times out, introduce chunking?

**Options**:
A. Keep atomic (all-or-nothing), optimize transaction
B. Introduce chunked publish (25 teachers at a time), partial success possible
C. Hybrid: atomic up to threshold, chunked beyond

**Recommendation**: Option A initially, revisit if performance testing shows need

---

## 20. CONCLUSION

### 20.1 Architectural Principles Summary

✅ **Single Canonical Model**: All sources → `CanonicalTimetable` → `ValidationEngine`  
✅ **No Guessing**: Ambiguous data → explicit ERROR, never silent inference  
✅ **Atomic Publishing**: Validate all → publish all or nothing  
✅ **Single Validation Engine**: Same rules for imports AND direct edits  
✅ **Source Traceability**: Every error references exact source location  

### 20.2 Key Decisions

1. **Resource Management**: Normalized entity with explicit resolution (NO auto-create)
2. **Teacher Identity**: Department-scoped uniqueness (same acronym, different departments OK)
3. **DOCX Parsing**: Structure-aware (WordprocessingML), detects ambiguity, never guesses
4. **Validation Engine**: Comprehensive rule set, ERROR/WARNING/INFO severity
5. **Bulk Operations**: Department-wide validation and atomic publishing
6. **Direct Editing**: Uses same canonical model and validation as imports

### 20.3 What Changed from Previous Architecture

**Major Corrections**:
- ❌ Removed two-person team split (Koushik/Lipika)
- ✅ Changed to solo implementation with dependency sequencing
- ❌ Removed `ImportPreview` as canonical model
- ✅ Introduced proper `CanonicalTimetable` domain model
- ❌ Removed resource auto-creation
- ✅ Explicit resolution: RESOLVED/UNRESOLVED/AMBIGUOUS
- ❌ Removed casual chunked publishing
- ✅ Atomic publish-all-or-nothing as initial design
- ❌ Removed resource type inference as authoritative
- ✅ Marked type inference as heuristic only
- ❌ Removed "Shared" as fake department
- ✅ NULL department = shared resource (proper modeling)

### 20.4 Next Steps

1. **Review this architecture document** with stakeholders
2. **Clarify open decisions** (section 19)
3. **Begin Phase 2 Foundation** (weeks 1-2): Define domain model, refactor XLSX adapter
4. **Proceed with dependency-driven implementation** (section 14)

---

**END OF PHASE 2 ARCHITECTURE (CORRECTED VERSION 2.0)**

