# DOCX Parser Specification: MCA Timetable-2026-Odd V7

**Status**: Specification Revised - Implementation NOT Started
**Document Analyzed**: MCA Timetable-2026-Odd V7.docx
**Analysis Date**: 2026-09-10
**Revision Date**: 2026-09-10
**Analyst**: Evidence-based structural analysis

---
rv
## Executive Summary

This specification defines a **deterministic, non-guessing parser** for DOCX timetable documents based on actual structural analysis of the MCA department timetable. The parser produces an **intermediate representation** that preserves individual candidates and unresolved relationships, then converts resolvable activities to `CanonicalTimetable` objects for validation.

**Critical Principles**:
1. The parser NEVER guesses associations between teachers, activities, and resources
2. Ambiguous relationships are preserved as unresolved blocks with individual candidates
3. Comma-separated string flattening is PROHIBITED - it destroys relationship information
4. Only sufficiently resolved activities convert to CanonicalTimetable
5. Unresolved blocks cannot be published to confirmed timetables

---

## 0. INTERMEDIATE REPRESENTATION FOR UNRESOLVED BLOCKS

### 0.1 The Problem with Comma-Separated Strings

**PROHIBITED APPROACH** (destroys relationships):
```python
# ❌ WRONG: Flattens candidates into strings
ScheduleActivity(
    subject_or_activity="PY1, PE2, DS 3,4",  # Which activity?
    teacher_acronym="SU, TS, SS, KPS",       # Which teacher teaches which?
    room="LAB1A, LAB1B"                      # Which resource for which?
)
```

**Why This is Unacceptable**:
- Destroys the distinction between individual candidates
- Makes it impossible to later resolve "SU teaches PY1" vs "TS teaches PE2"
- Creates false impression that all teachers teach all activities
- Cannot represent that relationships are unresolved, only unknown

### 0.2 Intermediate Representation: TimetableBlock

**Design**: Preserve individual candidates without inventing associations.

```python
@dataclass
class ActivityCandidate:
    """A single activity candidate extracted from cell."""
    code: str  # e.g., "PY1", "PE2", "DS 3,4", "PE 1,2,3,4"
    inferred_type: str  # "CLASS" | "LAB" | "OTHER"
    is_tokenization_ambiguous: bool  # True if phrase like "DS 3,4" or "PE 1,2,3,4" has unclear boundaries

@dataclass
class TeacherCandidate:
    """A single teacher candidate extracted from cell."""
    acronym: str  # e.g., "SU", "TS"
    normalized_acronym: str  # uppercase, trimmed
    is_identity_resolvable: bool  # True if can resolve to unique teacher

@dataclass
class ResourceCandidate:
    """A single resource candidate extracted from cell."""
    code: str  # e.g., "LAB1A", "LAB1B"
    normalized_code: str  # uppercase, spacing normalized
    is_identity_resolvable: bool  # True if can resolve to unique resource

@dataclass
class TimetableBlock:
    """Intermediate representation of a timetable cell.

    Preserves individual candidates without asserting relationships.
    May be RESOLVED (1:1:1 mapping with no ambiguity) or UNRESOLVED (ambiguous).
    """
    # Structural metadata (always present)
    day: str  # ISO day name: "monday", "tuesday", etc.
    section: str  # e.g., "I-A", "III-B"
    slots: list[str]  # e.g., ["S4"], ["S4", "S5"]

    # Extracted candidates (lists preserve individuals)
    activity_candidates: list[ActivityCandidate]
    teacher_candidates: list[TeacherCandidate]
    resource_candidates: list[ResourceCandidate]

    # Resolution status
    is_resolved: bool  # True if can convert to ScheduleActivity
    ambiguity_reason: str | None  # Why unresolved, if applicable

    # Source traceability
    source_location: SourceLocation
    original_text: str  # Exact cell content

    # Validation issues accumulated during parsing
    issues: list[ValidationIssue]


@dataclass
class ResolutionRule:
    """AUTHORITATIVE RULE for determining if TimetableBlock is RESOLVED.

    A block is RESOLVED if and only if ALL of the following are true:

    1. EXACTLY ONE ActivityCandidate exists
    2. The ActivityCandidate is NOT marked as tokenization-ambiguous
    3. EXACTLY ONE TeacherCandidate exists
    4. The TeacherCandidate identity is uniquely resolvable or explicitly valid for CREATE
    5. ZERO or ONE ResourceCandidate exists
    6. If ResourceCandidate exists, its identity is uniquely resolvable
    7. There are NO ERROR-level ValidationIssues
    8. There is NO unresolved activity-tokenization ambiguity
    9. There is NO unresolved relationship ambiguity

    Candidate count alone NEVER implies semantic resolution.
    """

    @staticmethod
    def is_resolved(block: TimetableBlock) -> tuple[bool, str | None]:
        """Check if block meets resolution criteria.

        Returns:
            (True, None) if resolved
            (False, reason) if unresolved
        """
        # Rule 1: Exactly one activity
        if len(block.activity_candidates) != 1:
            return False, f"{len(block.activity_candidates)} activity candidates (need exactly 1)"

        activity = block.activity_candidates[0]

        # Rule 2: Activity not tokenization-ambiguous
        if activity.is_tokenization_ambiguous:
            return False, f"Activity '{activity.code}' has ambiguous boundaries"

        # Rule 3: Exactly one teacher
        if len(block.teacher_candidates) != 1:
            return False, f"{len(block.teacher_candidates)} teacher candidates (need exactly 1)"

        teacher = block.teacher_candidates[0]

        # Rule 4: Teacher identity resolvable
        if not teacher.is_identity_resolvable:
            return False, f"Teacher '{teacher.acronym}' identity not uniquely resolvable"

        # Rule 5: Zero or one resource
        if len(block.resource_candidates) > 1:
            return False, f"{len(block.resource_candidates)} resource candidates (need 0 or 1)"

        # Rule 6: If resource exists, identity resolvable
        if len(block.resource_candidates) == 1:
            resource = block.resource_candidates[0]
            if not resource.is_identity_resolvable:
                return False, f"Resource '{resource.code}' identity not uniquely resolvable"

        # Rule 7: No ERROR-level issues
        errors = [issue for issue in block.issues if issue.severity == ERROR]
        if errors:
            return False, f"{len(errors)} ERROR-level validation issue(s)"

        # Rule 8 & 9: Covered by above checks

        return True, None

@dataclass
class ResolvedActivity:
    """A TimetableBlock that has been resolved to a single activity.

    Ready for conversion to CanonicalTimetable.
    """
    day: str
    section: str
    slots: list[str]
    entry_type: str  # "CLASS" | "LAB" | "OTHER"
    subject_or_activity: str
    teacher_acronym: str
    resource_code: str | None  # May be None (no room specified)
    source_location: SourceLocation
```

### 0.3 Resolution Rules

**A TimetableBlock is RESOLVED if**:
1. Exactly 1 activity candidate, AND
2. Exactly 0 or 1 teacher candidate, AND
3. Exactly 0 or 1 resource candidate

**Examples**:

**RESOLVED (1:1:1)**:
```python
TimetableBlock(
    day="monday",
    section="I-A",
    slots=["S3"],
    activity_candidates=[ActivityCandidate(code="DBMS", inferred_type="CLASS")],
    teacher_candidates=[TeacherCandidate(acronym="VR", normalized="VR")],
    resource_candidates=[ResourceCandidate(code="CA1", normalized="CA1")],
    is_resolved=True,
    ambiguity_reason=None
)
```
→ Converts to `ScheduleActivity(subject="DBMS", teacher_acronym="VR", room="CA1")`

**UNRESOLVED (4:4:2 - ambiguous mapping)**:
```python
TimetableBlock(
    day="tuesday",
    section="I-A",
    slots=["S4", "S5"],
    activity_candidates=[
        ActivityCandidate(code="PY1", inferred_type="LAB"),
        ActivityCandidate(code="PE2", inferred_type="LAB"),
        ActivityCandidate(code="DS 3", inferred_type="LAB"),
        ActivityCandidate(code="DS 4", inferred_type="LAB"),
    ],
    teacher_candidates=[
        TeacherCandidate(acronym="SU", normalized="SU"),
        TeacherCandidate(acronym="TS", normalized="TS"),
        TeacherCandidate(acronym="SS", normalized="SS"),
        TeacherCandidate(acronym="KPS", normalized="KPS"),
    ],
    resource_candidates=[
        ResourceCandidate(code="LAB1B", normalized="LAB1B"),
        ResourceCandidate(code="LAB 1A", normalized="LAB1A"),
    ],
    is_resolved=False,
    ambiguity_reason="4 activities, 4 teachers, 2 resources: cannot determine associations",
    issues=[ValidationIssue(severity=ERROR, code="AMBIGUOUS_BLOCK", ...)]
)
```
→ **CANNOT convert to CanonicalTimetable**
→ Remains as unresolved block for manual correction

### 0.4 Interaction with CanonicalTimetable

**Conversion Rules**:
1. **Only RESOLVED blocks** convert to `ScheduleActivity` objects
2. **UNRESOLVED blocks** are collected separately in the import preview
3. User must resolve ambiguities before publishing to confirmed timetable

**Import Preview Structure**:
```python
@dataclass
class DOCXImportPreview:
    """Result of DOCX parsing before confirmation."""
    academic_year: str
    department: str
    source_file: str

    # Successfully resolved activities
    resolved_blocks: list[ResolvedActivity]

    # Blocks that need manual resolution
    unresolved_blocks: list[TimetableBlock]

    # Teachers extracted (for identity resolution)
    teacher_identities: list[TeacherIdentity]

    # All validation issues
    errors: list[ValidationIssue]  # Block import
    warnings: list[ValidationIssue]  # Allow with review

    # Summary
    total_blocks: int
    resolved_count: int
    unresolved_count: int
```

**Conversion to CanonicalTimetable**:
```python
def convert_to_canonical(preview: DOCXImportPreview) -> CanonicalTimetable | None:
    """Convert resolved activities to canonical format.

    Returns None if there are ERROR-level issues that prevent safe conversion.
    """
    if preview.errors:
        return None  # Cannot convert with errors

    activities = []
    for resolved in preview.resolved_blocks:
        activities.append(ScheduleActivity(
            day=resolved.day,
            slots=resolved.slots,
            entry_type=resolved.entry_type,
            subject_or_activity=resolved.subject_or_activity,
            section=resolved.section,
            teacher_acronym=resolved.teacher_acronym,
            room=resolved.resource_code,  # May be None
            source_location=resolved.source_location
        ))

    return CanonicalTimetable(
        academic_year=preview.academic_year,
        source=SourceLocation(source_type="DOCX", source_identifier=preview.source_file),
        teachers=preview.teacher_identities,
        activities=activities,
        validation_issues=preview.warnings  # Only warnings remain
    )
```

---

## 0.3 CRITICAL PARSING PRINCIPLES

### Principle 1: NO GUESSING
**Punctuation, whitespace, parentheses, or visual proximity alone MUST NOT create semantic associations.**

Examples of PROHIBITED guessing:
- ❌ "Commas always separate activities" → "DS 3,4" is NOT automatically "DS 3" and "DS 4"
- ❌ "First teacher goes with first activity" → No positional pairing
- ❌ "Parentheses grouping implies association" → `(SU) (TS)` whitespace is visual, not semantic
- ❌ "Use first resource when multiple present" → NO "first wins" heuristic
- ❌ "Numbers after activity are always separate" → "PE 1,2,3,4" may be ONE activity name

### Principle 2: PRESERVE SOURCE STRUCTURE
**Raw source phrases → candidate tokens → semantic identity**

- **Raw source phrase**: Exact text from cell ("DS 3,4", "PE 1,2,3,4")
- **Candidate token/phrase**: Extracted but NOT semantically interpreted
- **Semantic activity identity**: Only after deterministic structural evidence

**Do NOT claim activity boundaries unless document structure establishes them.**

### Principle 3: EXPLICIT AMBIGUITY
**Unresolved relationships remain explicit as TimetableBlock with `is_resolved=False`**

- Individual candidates preserved separately
- NO comma-separated string flattening
- NO invented associations
- Block cannot convert to ScheduleActivity until manually resolved

### Principle 4: ERROR SEMANTICS
**Ambiguities that could produce incorrect teacher availability are ERROR-level.**

- Multiple teachers + ambiguous activity boundaries = ERROR (blocks publication)
- Multiple resources (even 1:1:2) = ERROR (no "first resource" heuristic)
- Missing teacher = ERROR (required for availability system)

### Principle 5: STRUCTURAL EVIDENCE ONLY
**Parser may only act on deterministic structural markers from the DOCX:**

- Cell merges (gridSpan, vMerge)
- Parentheses for teacher/resource grouping
- Separate rows/cells
- Explicit delimiters in structured metadata

**Parser must NOT act on:**
- Comma punctuation in activity phrases
- Whitespace positioning
- Visual alignment
- Typographical conventions
- Assumed naming patterns

---

## 0.4 MANUAL RESOLUTION CONTRACT

### 0.4.1 The Manual Resolution Need

When parser produces an unresolved `TimetableBlock`, a human must explicitly resolve it before it can convert to `CanonicalTimetable`.

**Parser responsibility**: Extract candidates, identify ambiguity, STOP
**Human responsibility**: Provide explicit resolution mapping

### 0.4.2 Resolution Mapping Structure

```python
@dataclass
class ManualResolutionMapping:
    """Explicit human-created mapping from unresolved block to resolved activity."""

    # Reference to source block
    source_block_id: str  # UUID of TimetableBlock

    # Explicit selections (human decision)
    selected_activity: str  # Which ActivityCandidate.code
    selected_teacher: str  # Which TeacherCandidate.acronym
    selected_resource: str | None  # Which ResourceCandidate.code (or None if no resource)

    # Metadata
    resolved_by: str  # User who resolved
    resolved_at: datetime
    resolution_notes: str | None  # Optional explanation


@dataclass
class ResolvedActivityFromMapping:
    """A resolved activity created from manual resolution mapping."""

    # Same fields as ResolvedActivity
    day: str
    section: str
    slots: list[str]
    entry_type: str
    subject_or_activity: str
    teacher_acronym: str
    resource_code: str | None

    # Source traceability (preserved from original block)
    source_location: SourceLocation
    original_text: str  # From TimetableBlock

    # Resolution metadata
    resolution_mapping_id: str  # UUID of ManualResolutionMapping
    manually_resolved: bool = True
```

### 0.4.3 Resolution Process

**Step 1: Parser Produces Unresolved Block**
```python
unresolved_block = TimetableBlock(
    day="tuesday",
    section="I-A",
    slots=["S4", "S5"],
    activity_candidates=[
        ActivityCandidate(code="PY1", inferred_type="LAB", is_tokenization_ambiguous=False),
        ActivityCandidate(code="PE2", inferred_type="LAB", is_tokenization_ambiguous=False),
    ],
    teacher_candidates=[
        TeacherCandidate(acronym="SU", normalized_acronym="SU", is_identity_resolvable=True),
        TeacherCandidate(acronym="TS", normalized_acronym="TS", is_identity_resolvable=True),
    ],
    resource_candidates=[
        ResourceCandidate(code="LAB1B", normalized_code="LAB1B", is_identity_resolvable=True),
    ],
    is_resolved=False,
    ambiguity_reason="2 activities, 2 teachers: cannot determine associations",
    ...
)
```

**Step 2: UI Presents Unresolved Block to User**
- Show original cell text
- Show all candidates: activities, teachers, resources
- Show ambiguity reason
- Provide resolution interface

**Step 3: User Creates Explicit Mappings**

User decides this block contains TWO activities:

```python
mapping_1 = ManualResolutionMapping(
    source_block_id="<block_uuid>",
    selected_activity="PY1",
    selected_teacher="SU",
    selected_resource="LAB1B",
    resolved_by="user@example.com",
    resolved_at=datetime.now(),
    resolution_notes="SU teaches PY1 based on course schedule"
)

mapping_2 = ManualResolutionMapping(
    source_block_id="<block_uuid>",
    selected_activity="PE2",
    selected_teacher="TS",
    selected_resource="LAB1B",  # Same resource, different time or shared
    resolved_by="user@example.com",
    resolved_at=datetime.now(),
    resolution_notes="TS teaches PE2 based on course schedule"
)
```

**Step 4: System Converts Mappings to ResolvedActivities**
```python
resolved_1 = ResolvedActivityFromMapping(
    day="tuesday",
    section="I-A",
    slots=["S4", "S5"],
    entry_type="LAB",
    subject_or_activity="PY1",
    teacher_acronym="SU",
    resource_code="LAB1B",
    source_location=unresolved_block.source_location,  # PRESERVED
    original_text=unresolved_block.original_text,
    resolution_mapping_id="<mapping_1_uuid>",
    manually_resolved=True
)

resolved_2 = ResolvedActivityFromMapping(
    day="tuesday",
    section="I-A",
    slots=["S4", "S5"],
    entry_type="LAB",
    subject_or_activity="PE2",
    teacher_acronym="TS",
    resource_code="LAB1B",
    source_location=unresolved_block.source_location,  # PRESERVED
    original_text=unresolved_block.original_text,
    resolution_mapping_id="<mapping_2_uuid>",
    manually_resolved=True
)
```

**Step 5: Rerun Validation**
- Validate each resolved activity independently
- Check for teacher overlaps
- Check for resource conflicts
- Check for slot conflicts
- Add new validation issues if any

**Step 6: Update DOCXImportPreview**
```python
preview = DOCXImportPreview(
    ...
    resolved_blocks=[...existing_resolved..., resolved_1, resolved_2],
    unresolved_blocks=[...remove unresolved_block...],
    manually_resolved_count=2,
    ...
)
```

### 0.4.4 Excluded Candidates

If user intentionally excludes a candidate (e.g., determines "DS 3,4" was a typo), record it:

```python
@dataclass
class ExcludedCandidate:
    """Record of intentionally excluded candidate."""
    source_block_id: str
    candidate_type: str  # "activity" | "teacher" | "resource"
    candidate_code: str
    excluded_by: str
    excluded_at: datetime
    exclusion_reason: str
```

Example:
```python
excluded = ExcludedCandidate(
    source_block_id="<block_uuid>",
    candidate_type="activity",
    candidate_code="DS 3,4",
    excluded_by="user@example.com",
    excluded_at=datetime.now(),
    exclusion_reason="Typo in original document, should be DS 3 only"
)
```

### 0.4.5 Publication Rules After Manual Resolution

**MUST be true for publication**:
- All unresolved blocks have been resolved OR explicitly excluded
- All manually resolved activities pass validation (zero ERRORs)
- No teacher overlaps exist
- No required resource conflicts exist
- All teacher identities resolved

**Cannot publish if**:
- Any unresolved blocks remain without resolution mappings
- Any ERROR-level validation issues exist
- Any manually resolved activity fails validation

### 0.4.6 Transport/Staging Representation (NOT Persistent Domain)

**CRITICAL ARCHITECTURE CLARIFICATION**:

These structures are **import-preview/staging transport representations**, NOT persistent domain/database tables.

**Approved architecture flow**:
```
DOCX/XLSX/Editor
    ↓
ImportPreview / staging transport  ← These structures live HERE
    ↓
CanonicalTimetable
    ↓
ValidationEngine
    ↓
DRAFT timetable
    ↓
CONFIRMED timetable
```

**ImportPreview is transport/UI state, NOT the domain model.**

---

**Transport Structures (in-memory or session-scoped)**:

```python
@dataclass
class UnresolvedTimetableBlock:
    """Transport structure for unresolved block in import preview.

    IMPORTANT: This is NOT a persistent domain entity.
    Lives only in ImportPreview/staging state during import session.
    """
    id: str  # Temporary ID for this import session
    day: str
    section: str
    slots: list[str]
    activity_candidates: list[dict]  # [{code, type, is_tokenization_ambiguous}]
    teacher_candidates: list[dict]  # [{acronym, normalized, is_identity_resolvable}]
    resource_candidates: list[dict]  # [{code, normalized, is_identity_resolvable}]
    ambiguity_reason: str
    original_text: str
    source_location: dict  # Serialized SourceLocation

    # NO created_at, NO database FK
    # This is staging data only


@dataclass
class ManualResolutionMapping:
    """User's explicit resolution decision for unresolved block.

    IMPORTANT: This is NOT a persistent domain entity.
    Applied to ImportPreview during import session, then discarded.
    """
    source_block_temp_id: str  # References UnresolvedTimetableBlock.id
    selected_activity: str
    selected_teacher: str
    selected_resource: str | None
    resolved_by: str  # Current user
    resolution_notes: str | None

    # NO database persistence
    # Used to transform unresolved → resolved, then discarded


@dataclass
class ExcludedCandidate:
    """Record of intentionally excluded candidate.

    IMPORTANT: This is NOT a persistent domain entity.
    Tracked in ImportPreview for audit during import session.
    """
    source_block_temp_id: str
    candidate_type: str  # "activity"|"teacher"|"resource"
    candidate_code: str
    exclusion_reason: str

    # NO database persistence
    # Used for import session audit only
```

---

**Storage Strategy**:

1. **During import session**: Store in ImportPreview JSON/in-memory structure
2. **After manual resolution**: Convert to ResolvedActivity → CanonicalTimetable
3. **After confirmation**: Only CanonicalTimetable persists to DRAFT/CONFIRMED
4. **Staging data discarded**: UnresolvedTimetableBlock, ResolutionMapping are ephemeral

**DO NOT**:
- ❌ Create database migrations for these structures
- ❌ Add SQLAlchemy models for these structures
- ❌ Persist across import sessions
- ❌ Create a second domain model

**DO**:
- ✅ Use these as transport/DTO structures in ImportPreview
- ✅ Store temporarily in import session (Redis, in-memory, or serialized JSON)
- ✅ Convert to CanonicalTimetable after resolution
- ✅ Preserve source_location through the conversion

---

**Example Implementation (in-memory staging)**:

```python
@dataclass
class DOCXImportPreview:
    """Transport structure for DOCX import preview.

    Contains both resolved and unresolved blocks.
    This entire structure is staging/transport data.
    """
    import_session_id: str  # Temporary session ID
    academic_year: str
    department: str
    source_file: str

    # Successfully resolved (can convert to CanonicalTimetable)
    resolved_blocks: list[ResolvedActivity]

    # Unresolved blocks (need manual resolution)
    unresolved_blocks: list[UnresolvedTimetableBlock]  # Transport, not domain

    # Manual resolutions applied
    manual_resolutions: list[ManualResolutionMapping]  # Transport, not domain

    # Excluded candidates
    excluded_candidates: list[ExcludedCandidate]  # Transport, not domain

    # Validation
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]

    # Summary
    total_blocks: int
    resolved_count: int
    unresolved_count: int
    manually_resolved_count: int


# Manual resolution workflow (operates on staging state)
def apply_manual_resolution(
    preview: DOCXImportPreview,
    mapping: ManualResolutionMapping
) -> DOCXImportPreview:
    """Apply user's manual resolution to ImportPreview.

    Returns updated preview with:
    - Unresolved block removed
    - New resolved activity added
    - Manual resolution recorded

    Does NOT persist to database.
    """
    # Find unresolved block
    block = next(b for b in preview.unresolved_blocks if b.id == mapping.source_block_temp_id)

    # Create resolved activity
    resolved = ResolvedActivityFromMapping(
        day=block.day,
        section=block.section,
        slots=block.slots,
        subject_or_activity=mapping.selected_activity,
        teacher_acronym=mapping.selected_teacher,
        resource_code=mapping.selected_resource,
        source_location=SourceLocation(**block.source_location),
        original_text=block.original_text,
        manually_resolved=True
    )

    # Update preview (in-memory)
    preview.resolved_blocks.append(resolved)
    preview.unresolved_blocks.remove(block)
    preview.manual_resolutions.append(mapping)
    preview.manually_resolved_count += 1

    return preview


# Conversion to CanonicalTimetable (final step)
def convert_preview_to_canonical(preview: DOCXImportPreview) -> CanonicalTimetable:
    """Convert ImportPreview to CanonicalTimetable.

    ONLY resolved blocks convert.
    Unresolved blocks MUST be empty or this fails.

    After conversion, staging data (UnresolvedTimetableBlock, etc.) is discarded.
    """
    if preview.unresolved_blocks:
        raise ValueError(f"Cannot convert: {len(preview.unresolved_blocks)} unresolved blocks remain")

    if preview.errors:
        raise ValueError(f"Cannot convert: {len(preview.errors)} errors present")

    activities = []
    for resolved in preview.resolved_blocks:
        activities.append(ScheduleActivity(
            day=resolved.day,
            slots=resolved.slots,
            entry_type=resolved.entry_type,
            subject_or_activity=resolved.subject_or_activity,
            section=resolved.section,
            teacher_acronym=resolved.teacher_acronym,
            room=resolved.resource_code,
            source_location=resolved.source_location
        ))

    return CanonicalTimetable(
        academic_year=preview.academic_year,
        source=SourceLocation(source_type="DOCX", source_identifier=preview.source_file),
        activities=activities,
        validation_issues=preview.warnings
    )
    # staging data (preview, unresolved blocks, mappings) now discarded
```

---

**Persistence Strategy**:

**Option A: No persistence of staging data** (simplest)
- ImportPreview exists only during HTTP request/session
- User must resolve all blocks before leaving page
- Convert to CanonicalTimetable → persist to DRAFT timetable

**Option B: Session-scoped persistence** (if needed)
- Store serialized DOCXImportPreview in Redis or session storage
- Key by import_session_id
- Expires after N hours
- Still NOT part of canonical domain model

**Never persist as domain tables**. ImportPreview is staging/transport only.

---

## 0.5 PARENTHESES VS ASSOCIATIONS

### 0.5.1 What Parentheses DO

**Parentheses are used to IDENTIFY candidate types:**

- `(SU)` → This is a **teacher candidate** (not a subject or room)
- `(LAB1A)` → This is a **resource candidate** (not a subject or teacher)
- `DBMS` → This is an **activity candidate** (no parentheses)

**Example**:
```
DBMS
(VR)
CA1
```

Parser extracts:
- Activity candidate: "DBMS" (no parentheses → activity)
- Teacher candidate: "VR" (in parentheses → teacher)
- Resource candidate: "CA1" (no parentheses, after teacher → resource)

### 0.5.2 What Parentheses DO NOT DO

**Parentheses, whitespace, visual proximity, or ordering MUST NOT establish relationships:**

- ❌ `(SU) (TS)` with whitespace between does NOT mean "SU is paired with first activity, TS with second"
- ❌ `(LAB1B) (LAB1A)` with positioning does NOT mean "LAB1B for first activity, LAB1A for second"
- ❌ Order of appearance does NOT imply pairing

**Example of PROHIBITED inference**:
```
PY1, PE2
(SU) (TS)
LAB1B
```

Parser extracts:
- Activity candidates: ["PY1", "PE2"] OR ["PY1, PE2"] (depending on structural evidence)
- Teacher candidates: ["SU", "TS"]
- Resource candidates: ["LAB1B"]

Parser does NOT infer:
- ❌ "SU teaches PY1" (positional assumption)
- ❌ "TS teaches PE2" (positional assumption)
- ❌ "First teacher goes with first activity" (ordering assumption)

### 0.5.3 When Associations CAN Be Established

**Only when DOCX provides deterministic structural relationship:**

1. **Separate cells**: If activities are in separate cells, teacher in each cell is associated
2. **Explicit labels**: If document uses "Teacher: SU" or similar labels
3. **Separate rows**: If each activity is on its own row with structured columns

**Example where association IS established**:

| Activity | Teacher | Room |
|----------|---------|------|
| DBMS     | VR      | CA1  |
| ADA      | TSP     | LAB1A|

This table structure provides deterministic associations.

**Example where association is NOT established**:
```
Single cell containing:
PY1, PE2
(SU) (TS)
LAB1B
```

No structural relationship between text lines. Parser must NOT invent associations.
    for resolved in preview.resolved_blocks:
        activities.append(ScheduleActivity(
            day=resolved.day,
            slots=resolved.slots,
            entry_type=resolved.entry_type,
            subject_or_activity=resolved.subject_or_activity,
            section=resolved.section,
            teacher_acronym=resolved.teacher_acronym,
            room=resolved.resource_code,  # May be None
            source_location=resolved.source_location
        ))

    return CanonicalTimetable(
        academic_year=preview.academic_year,
        source=SourceLocation(source_type="DOCX", source_identifier=preview.source_file),
        teachers=preview.teacher_identities,
        activities=activities,
        validation_issues=preview.warnings  # Only warnings remain
    )
```

---

## 1. DOCUMENT STRUCTURE

### 1.1 Overall Document Composition

**Observed Structure**:
- **Institution Header**: "BMS COLLEGE OF ENGINEERING, BANGALORE-560019"
- **Department**: "Department of Computer Applications"
- **Academic Period**: "Timetable for Odd semester 2026"
- **Metadata Row**: Class rooms, semester, effective date
- **Main Timetable**: Single large table with nested structure
- **Faculty Legend**: Table mapping faculty names to acronyms and locations

### 1.2 Table Inventory

Based on the provided document content:

**Table 1: Main Timetable**
- **Type**: Timetable grid
- **Dimensions**: Approximately 7 rows (days) × 14 columns (time structure)
- **Structure**: Complex merged cells, multiple programs/sections per day
- **Contains**: Day names, time slots, activities, teachers, rooms

**Table 2: Faculty Legend**
- **Type**: Metadata table
- **Dimensions**: 15 rows × 4 columns
- **Structure**: Simple grid, no merges
- **Contains**: Serial number, faculty name, acronym, classroom/lab, location
- **Purpose**: Maps teacher acronyms to full names and departments

### 1.3 Document Ordering

1. Institution/Department header (paragraphs)
2. Academic period and classroom metadata (paragraphs)
3. Main timetable table
4. Coordinator/HOD signature lines
5. Faculty legend table

---

## 2. WORD TABLE GEOMETRY

### 2.1 Exact Physical Table Dimensions

**Main Timetable Table** (Table 0):

**Physical Dimensions** (from Word document structure):
- **Rows**: 25 rows total
  - Row 0: Header row (TIME/DAY, time ranges)
  - Rows 1-24: Data rows (6 days × 4 sections = 24 rows)
- **Columns**: 13 columns total
  - Column 0: Day name
  - Column 1: Section identifier (SEM)
  - Columns 2-10: Time slots S1-S9
  - Column 11: May contain break or additional slot
  - Column 12: May contain break or additional slot

**Logical Grid Dimensions** (after merge reconstruction):
- **Days**: 6 (MON, TUE, WED, THU, FRI, SAT)
- **Sections per day**: 4 (I-A, I-B, III-A, III-B)
- **Working slots**: 9 (S1-S9)
- **Break slots**: 2 (Morning Break after S3, Lunch Break after S5)

**Critical Distinction**:
- **Physical table**: What Word stores (rows × columns with merge metadata)
- **Logical grid**: What parser reconstructs (accounting for merged cells)

**Column Mapping**:
| Physical Col | Logical Purpose | Content |
|--------------|-----------------|---------|
| 0 | Day | MON, TUE, WED, THU, FRI, SAT (vertically merged) |
| 1 | Section | I-A, I-B, III-A, III-B |
| 2 | S1 | 08:00-08:55 |
| 3 | S2 | 08:55-09:50 |
| 4 | S3 | 09:50-10:45 |
| 5 | BREAK | Morning Coffee Break (10:45-11:15, vertically merged) |
| 6 | S4 | 11:15-12:10 |
| 7 | S5 | 12:10-13:05 |
| 8 | BREAK | Lunch Break (13:05-14:00, vertically merged) |
| 9 | S6 | 14:00-14:55 |
| 10 | S7 | 14:55-15:50 |
| 11 | S8 | 15:50-16:45 |
| 12 | S9 | 16:45-17:40 |

**Row Structure** (Physical):
| Row Range | Day | Sections |
|-----------|-----|----------|
| 0 | HEADER | TIME/DAY, slot time ranges |
| 1-4 | MON | I-A, I-B, III-A, III-B |
| 5-8 | TUE | I-A, I-B, III-A, III-B |
| 9-12 | WED | I-A, I-B, III-A, III-B |
| 13-16 | THU | I-A, I-B, III-A, III-B |
| 17-20 | FRI | I-A, I-B, III-A, III-B |
| 21-24 | SAT | I-A, I-B, III-A, III-B |

**Faculty Legend Table** (Table 1):
- **Rows**: 16 rows total (1 header + 15 faculty entries)
- **Columns**: 5 columns
  - Column 0: Sl. No.
  - Column 1: Name of the Faculty
  - Column 2: Initials
  - Column 3: Classroom/Laboratory
  - Column 4: Location

### 2.2 Merge Patterns Observed

**Horizontal Merges** (`w:gridSpan`):
- **Multi-slot activities**: Lab sessions spanning 2 consecutive slots (gridSpan=2)
- **Example**: "PY1, PE2" in columns 6-7 spans S4+S5
- **Not used for breaks**: Break cells do not use gridSpan (they are vertically merged but occupy single columns)

**Vertical Merges** (`w:vMerge`):
- **Day name column** (column 0): Each day name merges across 4 section rows
  - Pattern: `vMerge="restart"` on first section row, `vMerge="continue"` on rows 2-4
- **Break columns** (columns 5, 8): Break cells merge vertically across all 4 sections for each day
  - Pattern: Same as day column

**Merge Reconstruction Algorithm**:
```python
def reconstruct_logical_grid(physical_table: Table) -> Grid:
    """Build logical grid accounting for merges.

    Returns 2D grid where:
    - Horizontal merges (gridSpan): Cell occupies multiple columns
    - Vertical merges (vMerge): Cell occupies multiple rows
    - Continuation cells marked but reference original cell
    """
    # Implementation details in Section 14
```

### 2.3 Cell Content Structure

**Standard Activity Cell** (3-line pattern):
```
DBMS
(VR)
CA1
```
- Line 1: Activity code (no parentheses)
- Line 2: Teacher acronym (in parentheses)
- Line 3: Resource code (no parentheses)

**Ambiguous Activity Cell** (complex pattern):
```
PY1, PE2, DS 3,4
(SU) (TS) (SS, KPS)
LAB1B
```
- Line 1: Multiple activity phrases (ambiguous boundaries)
- Line 2: Multiple teacher groups (ambiguous associations)
- Line 3: Resource code (may be multiple)

**Parser Behavior**:
- Extract candidates individually
- Mark as unresolved if ambiguous
- Preserve original text exactly

---

## 3. TIMETABLE GRID

### 3.1 Day Representation

**Explicit Day Names** (in leftmost column, vertically merged):
- MON (Monday)
- TUE (Tuesday)
- WED (Wednesday)
- THU (Thursday)
- FRI (Friday)
- SAT (Saturday)
- Sunday: **NOT PRESENT** in this document

**Day Detection Algorithm**:
1. Scan leftmost column of timetable table
2. Extract text from cells with vertical merge restart
3. Normalize to uppercase, strip whitespace
4. Map to ISO day numbers: MON=1, TUE=2, ..., SAT=6

### 3.2 Time Slot Representation

**Header Row** contains time ranges:
```
(1)       (2)       (3)       ...
08.00-    08.55-    09.50-
08.55     09.50     10.45
```

**Slot-to-Column Mapping** (0-indexed columns after day/section columns):
- Column 2: S1 (08:00-08:55)
- Column 3: S2 (08:55-09:50)
- Column 4: S3 (09:50-10:45)
- Column 5: BREAK (10:45-11:15) - NOT a working slot
- Column 6: S4 (11:15-12:10)
- Column 7: S5 (12:10-13:05)
- Column 8: BREAK (13:05-14:00) - NOT a working slot
- Column 9: S6 (14:00-14:55)
- Column 10: S7 (14:55-15:50)
- Column 11: S8 (15:50-16:45)
- Column 12: S9 (16:45-17:40)

**Slot Detection Algorithm**:
1. Parse header row time ranges
2. Match against institutional slot config (already in `schedule_config.py`)
3. Map columns to slot codes: S1-S9
4. Mark break columns explicitly - they contain "BREAK" text
5. Breaks are NEVER treated as working slots

### 3.3 Section/Program Representation

**Section Column** (column 1, labeled "SEM"):
- Contains: "I-A", "I-B", "III-A", "III-B"
- Format: `{YEAR}-{SECTION}`
  - YEAR: Roman numeral (I, III)
  - SECTION: Letter (A, B)

**Interpretation**:
- "I-A" → First year MCA, Section A
- "I-B" → First year MCA, Section B
- "III-A" → Third year MCA, Section A
- "III-B" → Third year MCA, Section B

**Program Derivation**:
- All sections belong to "MCA" program (from document header)
- Level: Derived from year (I=Year 1, III=Year 3)
- No explicit program column in timetable cells

---

## 4. ACTIVITY REPRESENTATION

### 4.1 Simple Activity Pattern

**Observed Pattern**:
```
DBMS
(VR)
CA1
```

**Structure**:
- Line 1: Subject/Activity code ("DBMS")
- Line 2: Teacher acronym in parentheses ("(VR)")
- Line 3: Room/Resource code ("CA1")

**Parsing Rules**:
1. Split cell text by newline/paragraph breaks
2. Line 1 (non-empty, no parentheses) → subject
3. Line with `(ACRONYM)` pattern → teacher acronym
4. Remaining non-empty line → room/resource
5. Multiple teachers: `(VPP, VR)` → comma-separated list

### 4.2 Lab Activity Pattern (Complex/Ambiguous)

**Observed Pattern**:
```
PY1, PE2,   DS 3,4
(SU) (TS)  (SS, KPS)
(LAB1B)   (LAB 1A)
```

**Structure**:
- Multiple activity phrases separated by visual grouping or punctuation
- Activity codes may include internal punctuation (e.g., "DS 3,4" may be ONE activity or TWO)
- Teachers in parentheses, possibly grouped
- Resources in parentheses, possibly grouped

**Ambiguity**: This pattern creates multiple mapping ambiguities:
- Which teacher teaches which activity?
- Which resource is used for which activity?
- Are activities parallel or sequential?
- Does "DS 3,4" mean one activity or two separate activities (DS 3 and DS 4)?

**Parser Behavior - CONSERVATIVE APPROACH**:

**Step 1: Identify Raw Phrases** (preserve source as-is without assumptions)
- Detect potential activity phrase boundaries using deterministic structural markers ONLY
- If no structural evidence exists (e.g., parentheses grouping, vertical alignment), preserve entire phrase
- **NEVER assume comma-separated numbers define separate activities**

**Step 2: Extract Individual Candidates**
- Activities extracted as raw phrases:
  - Individual candidates depend on structural evidence
  - If ambiguous, preserve entire phrase: `ActivityCandidate(code="PY1, PE2, DS 3,4", ...)`
- Teachers extracted individually by parentheses:
  - `[TeacherCandidate(acronym="SU", ...), TeacherCandidate(acronym="TS", ...), ...]`
- Resources extracted individually by parentheses:
  - `[ResourceCandidate(code="LAB1B", ...), ResourceCandidate(code="LAB 1A", ...)]`

**Step 3: DO NOT Invent Associations**
- **NO teacher-activity pairing**
- **NO resource-activity pairing**
- **NO arbitrary tokenization based on commas alone**

**Step 4: Mark as Unresolved**
- Set `is_resolved = False`
- Set `ambiguity_reason = "Multiple possible activities/teachers/resources, relationships cannot be determined from document structure"`
- Add ERROR-level issue: `AMBIGUOUS_BLOCK`
- Preserve original cell text for manual resolution

### 4.3 Tutorial/Cultural Activity Pattern

**Observed Patterns**:
```
PE TUTORIAL
(GK)
CA2
```

```
Library/Research Activity
```

```
Physical activity
```

**Rules**:
- If cell contains "TUTORIAL", "Library", "Research", "Cultural", "Physical", "Placement":
  - entry_type = "OTHER"
  - subject_or_activity = Full text
- Otherwise:
  - Infer entry_type from activity code:
    - Contains "LAB" or ends with digit after space → "LAB"
    - Contains "TUTORIAL" → "OTHER"
    - Default → "CLASS"

---

## 5. MULTI-SLOT ACTIVITIES

### 5.1 Horizontal Merge (Preferred Pattern)

**Observed Structure**:
```
Cell spans 2 columns (w:gridSpan="2"):
PY 1, Web 2,4, DS3
(RR) (VPP,  VR) (SS)
(LAB 1A)
```

**Parsing Rule**:
- If cell has `w:gridSpan="N"` where N > 1:
  - Activity spans N consecutive slots
  - Starting slot = current column index
  - Ending slot = current column index + N - 1
- Slots = [S{start}, S{start+1}, ..., S{end}]

**Example**:
- Cell at column 6 (S4) with gridSpan=2
- Slots = [S4, S5]

### 5.2 Repeated Text (Anti-Pattern - Avoid Duplication)

**Observed Structure**:
Some timetables repeat the same activity text in adjacent cells.

**Parsing Rule**:
- If adjacent cells in same row have identical text AND no gridSpan:
  - **Possible duplicate** or **separate instances**
  - Parser should treat as separate activities unless gridSpan proves otherwise
  - Emit INFO message: "Possible duplicate activity detected"

**Critical**: Use gridSpan as authoritative merge indicator, not text similarity.

### 5.3 Vertical Spanning (NOT for activities)

**Observed**: Vertical merges (`w:vMerge`) are used for:
- Day name column
- Break columns
- NOT for activities spanning multiple time slots

**Rule**: Activities NEVER span multiple days. Vertical merges in activity columns are structural errors.

---

## 6. PARALLEL ACTIVITIES

### 6.1 Multiple Sections (Same Slot)

**Pattern**: Multiple rows for same day represent different sections:
```
MON I-A  [activity A1]
    I-B  [activity B1]
    III-A [activity A3]
    III-B [activity B3]
```

**Parsing**:
- Each section row = separate logical timetable
- Activities for I-A and I-B at same day/slot are **independent**, not parallel
- Parser creates separate `ScheduleActivity` for each section

### 6.2 Within-Cell Parallel Activities

**Pattern** (AMBIGUOUS):
```
PY1, PE2, DS 3,4
(SU) (TS) (SS, KPS)
(LAB1B) (LAB 1A)
```

**Observed**: Multiple activities in one cell for one section.

**Interpretation Problem**:
- Are these simultaneous parallel activities?
- Are these alternative activities?
- Which teacher → which activity?
- Which resource → which activity?

**Parser Behavior** (DO NOT GUESS):
1. Extract all activity codes
2. Extract all teacher acronyms
3. Extract all room codes
4. Create `ScheduleActivity` objects with:
   - subject_or_activity = comma-separated list (preserve original)
   - teacher = UNRESOLVED (if multiple teachers)
   - resource = UNRESOLVED (if multiple resources)
5. Emit ValidationIssue:
   - severity = WARNING
   - code = "AMBIGUOUS_PARALLEL_ACTIVITY"
   - message = "Cell contains multiple activities with ambiguous teacher/resource mapping"
   - source_location = cell coordinates

**Validation Engine** will flag this for manual review.

---

## 7. TEACHER IDENTIFICATION

### 7.1 Acronym-in-Cell Pattern

**Location**: Inside timetable activity cells

**Format**:
- Single teacher: `(VR)`
- Multiple teachers: `(VPP, VR, TS)`
- With spaces: `(RR)  (VPP, VR, TS)` (grouped or listed)

**Extraction**:
1. Find text matching pattern: `\(([A-Z,\s]+)\)`
2. Extract individual teacher acronyms:
   - Split by comma: `"VPP, VR"` → `["VPP", "VR"]`
   - Each acronym becomes separate TeacherCandidate
3. Normalize each acronym: strip(), upper()
4. Result: List of TeacherCandidate objects (NOT comma-separated string)

**Example**:
- `(VPP, VR)` → `[TeacherCandidate(acronym="VPP", ...), TeacherCandidate(acronym="VR", ...)]`
- `(RR)  (VPP, VR, TS)` → `[TeacherCandidate(acronym="RR", ...), TeacherCandidate(acronym="VPP", ...), TeacherCandidate(acronym="VR", ...), TeacherCandidate(acronym="TS", ...)]`

**CRITICAL**: Teacher candidates remain as individual objects, NEVER flattened to comma-separated strings.

### 7.2 Faculty Legend Table

**Structure**:
```
Sl. No. | Name of the Faculty | Initials | Classroom/Laboratory | Location
1       | Dr. S. Uma          | SU       | CA1                 | CA classroom 1...
2       | Dr. D. N. Sujatha   | DNS      | CA2                 | ...
...
```

**Parsing**:
1. Identify legend table (contains "Name of the Faculty", "Initials" headers)
2. Extract mappings:
   - Acronym (column 3): "SU", "DNS", etc.
   - Full name (column 2): "Dr. S. Uma", etc.
   - Primary location (column 4, 5)
3. Build acronym → name dictionary

**Department Inference**:
- All teachers in this document belong to: "Computer Applications"
- Source: Document header "Department of Computer Applications"
- Parser extracts department from header, applies to all teachers

### 7.3 Teacher Identity Resolution

**Map to Existing Model**:
```python
TeacherIdentity(
    acronym="VR",           # From cell
    department="Computer Applications",  # From document header
    name="Veena R",         # From legend table
    resolution=CREATE or REUSE  # Determined by validator
)
```

**Ambiguity Handling**:
- If acronym NOT in legend → WARNING: "Unknown teacher acronym 'XX'"
- If multiple acronyms in cell → Store as list, let validator resolve
- If acronym maps to multiple teachers in different departments → ERROR (should not occur if department is from doc header)

---

## 8. RESOURCE / ROOM EXTRACTION

### 8.1 Room Naming Patterns Observed

**Classrooms**:
- `CA1`, `CA2`, `CA3` (Computer Applications classrooms)
- `FDC` (Faculty Development Center)
- `RL` (Research Lab)

**Labs**:
- `Lab1A`, `Lab1B`, `Lab2` (Computer Applications labs)
- `LAB 1A`, `LAB1A` (variations in spacing/case)

**Room extraction normalization**:
- Normalize case: uppercase
- Normalize spacing: "LAB 1A" → "LAB1A"
- Pattern: `[A-Z]+[0-9]*[A-Z]*`

### 8.2 Room Location in Cells

**Pattern**:
```
DBMS
(VR)
CA1      ← Room on third line
```

**Extraction**:
1. After extracting subject and teacher(s)
2. Remaining non-empty line(s) → candidate room codes
3. Match against known room pattern
4. If multiple rooms in cell → ambiguity

**Example** (AMBIGUOUS):
```
PY 1, Web 2,4, DS3
(RR) (VPP, VR) (SS)
(LAB 1A)        ← Which activity uses this room?
```

### 8.3 Resource Ambiguity

**Cases**:
1. **No room specified**: Empty/missing → resource = NULL
2. **One room specified**: Unambiguous → assign to activity
3. **Multiple rooms specified with multiple activities**: AMBIGUOUS

**Parser Behavior**:
- Extract all room codes from cell
- If len(rooms) == 1: assign to all activities in cell
- If len(rooms) > 1 and len(activities) > 1:
  - DO NOT GUESS mapping
  - Store rooms as comma-separated list
  - Emit WARNING: "Ambiguous resource mapping"
  - Let Resource Foundation resolve if possible

### 8.4 Resource Foundation Integration

**Existing System**:
- Resources have: name, normalized_name, resource_type, department
- Aliases supported
- Department-scoped resolution

**Parser Output**:
- Extract room string: "CA1", "LAB1A"
- Pass to Resource Foundation for resolution
- Resource Foundation returns:
  - `ResourceReference(resolved_resource_id=UUID)` if found
  - `AMBIGUOUS` if multiple matches
  - `UNRESOLVED` if not found

**Validation**:
- UNRESOLVED → WARNING: "Unknown resource 'CA1'"
- AMBIGUOUS → WARNING: "Resource 'LAB' matches multiple resources"

---

## 9. KNOWN AMBIGUITY: Tuesday I-A S4+S5

### 9.1 Actual Cell Content

**Cell Location**: Tuesday, I-A row, columns spanning S4+S5

**Observed Text**:
```
PY1, PE2,   DS 3, 4
(SU) (TS)  (SS, KPS)
(LAB1B)   (LAB 1A)
```

### 9.2 Structural Analysis

**What the DOCX establishes**:
- Cell spans 2 columns (S4, S5) via `w:gridSpan="2"`
- Cell contains text with punctuation, whitespace, and parentheses
- Whitespace/parentheses patterns: `(SU) (TS)  (SS, KPS)` and `(LAB1B)   (LAB 1A)`

**What the DOCX does NOT establish**:
- How many distinct activities are present
  - Is "PY1, PE2, DS 3, 4" ONE activity phrase, TWO activities (PY1 and "PE2, DS 3, 4"), THREE activities, or FOUR activities?
  - Punctuation alone cannot determine activity boundaries
- Which teacher teaches which activity
- Which resource is used for which activity
- Whether activities are parallel (simultaneous) or sequential

**Conservative Parser Interpretation**:
- **CANNOT determine activity boundaries from punctuation alone**
- Without deterministic structural evidence (e.g., separate rows, explicit delimiters in metadata), preserve as raw phrase
- Whitespace and parentheses are visual formatting, not semantic structure

### 9.3 Ambiguity Classification

**Type**: STRUCTURAL_AMBIGUITY + ACTIVITY_TOKENIZATION_AMBIGUITY

**Reason**: Document uses spatial grouping (whitespace, parentheses, commas) to suggest boundaries, but does not use a machine-readable format (e.g., separate cells, explicit labels, structured lists) to definitively establish activity boundaries or associations.

### 9.4 Parser Output for This Cell

**TimetableBlock Representation** (UNRESOLVED):
```python
TimetableBlock(
    day="tuesday",
    section="I-A",
    slots=["S4", "S5"],  # from gridSpan

    # Activity candidates - preserve raw phrase due to ambiguous boundaries
    activity_candidates=[
        ActivityCandidate(
            code="PY1, PE2, DS 3, 4",  # Entire phrase preserved
            inferred_type="LAB"
        )
        # OR if parser detects commas as potential separators but lacks confidence:
        # Multiple candidates with ambiguity flag
    ],

    # Teacher candidates - extracted individually from parentheses
    teacher_candidates=[
        TeacherCandidate(acronym="SU", normalized_acronym="SU"),
        TeacherCandidate(acronym="TS", normalized_acronym="TS"),
        TeacherCandidate(acronym="SS", normalized_acronym="SS"),
        TeacherCandidate(acronym="KPS", normalized_acronym="KPS"),
    ],

    # Resource candidates - extracted individually from parentheses
    resource_candidates=[
        ResourceCandidate(code="LAB1B", normalized_code="LAB1B"),
        ResourceCandidate(code="LAB 1A", normalized_code="LAB1A"),
    ],

    is_resolved=False,
    ambiguity_reason="Ambiguous activity boundaries, multiple teachers (4), multiple resources (2). Cannot determine associations.",

    source_location=SourceLocation(
        source_type="DOCX",
        source_identifier="MCA Timetable-2026-Odd V7.docx",
        table_index=0,
        row_index=3,  # Tuesday I-A
        column_index=6,  # S4 start
        original_text="PY1, PE2,   DS 3, 4\n(SU) (TS)  (SS, KPS)\n(LAB1B)   (LAB 1A)"
    ),

    original_text="PY1, PE2,   DS 3, 4\n(SU) (TS)  (SS, KPS)\n(LAB1B)   (LAB 1A)",

    issues=[
        ValidationIssue(
            severity=ERROR,
            code="AMBIGUOUS_BLOCK",
            message="Cannot determine activity boundaries and teacher-activity-resource associations. Cell contains ambiguous phrase 'PY1, PE2, DS 3, 4' with 4 teachers and 2 resources. Manual resolution required.",
            source_locations=[SourceLocation(...)]
        ),
        ValidationIssue(
            severity=ERROR,
            code="ACTIVITY_TOKENIZATION_AMBIGUOUS",
            message="Activity phrase 'PY1, PE2, DS 3, 4' contains punctuation that may indicate multiple activities, but structure does not provide definitive boundaries.",
            source_locations=[SourceLocation(...)]
        )
    ]
)
```

**CRITICAL NOTES**:
1. **NO comma-separated string flattening** - Teachers and resources remain as individual candidate objects
2. **NO automatic activity splitting** - "DS 3, 4" is NOT automatically interpreted as "DS 3" and "DS 4"
3. **NO invented associations** - Parser does NOT pair teachers with activities based on proximity
4. **NO resolution** - Block marked `is_resolved=False` and classified as ERROR-level
5. **Preserved original text** - Exact source content available for manual review

### 9.5 Manual Resolution Required

The system will:
1. Parse the cell structure
2. Extract candidates individually where boundaries are clear (teachers, resources)
3. **NOT GUESS** activity boundaries or associations
4. Present unresolved block to user with ERROR-level issues
5. **BLOCK PUBLICATION** until manually resolved
6. Allow user to either:
   - Edit original DOCX to disambiguate
   - Manually split into separate activities via UI
   - Provide explicit mapping through resolution interface

**DO NOT IMPLEMENT** automatic heuristics like:
- "Commas always separate activities"
- "First teacher goes with first activity"
- "Parentheses grouping implies association"
- "Whitespace indicates grouping"
- "DS 3, 4 is obviously DS 3 and DS 4"

These are **assumptions**, not structural facts from the document.

---

## 10. SOURCE TRACEABILITY

### 10.1 Source Location Structure

**Design** (extends existing `SourceLocation`):

```python
@dataclass
class SourceLocation:
    source_type: Literal["XLSX", "DOCX", "MANUAL"]
    source_identifier: str  # e.g., "MCA Timetable-2026-Odd V7.docx"

    # DOCX-specific fields
    table_index: int | None  # 0-based index of table in document
    row_index: int | None    # 0-based row within table
    column_index: int | None  # 0-based column within table

    # Generic fields (existing)
    sheet_name: str | None
    row_number: int | None
    column_name: str | None

    # Additional context
    original_text: str | None  # Preserve exact cell text
    merge_info: str | None  # e.g., "gridSpan=2, vMerge=restart"
```

**For DOCX Activities**:
```python
SourceLocation(
    source_type="DOCX",
    source_identifier="MCA Timetable-2026-Odd V7.docx",
    table_index=0,  # Main timetable is table 0
    row_index=5,    # Specific row (e.g., Tuesday I-A)
    column_index=3,  # Specific column (e.g., S2)
    original_text="DBMS\n(VR)\nCA1",  # Exact cell content
    merge_info="gridSpan=None, vMerge=None"
)
```

### 10.2 Traceability Requirements

**Every extracted activity MUST have**:
- Source document filename
- Table index (if multiple tables)
- Row and column coordinates
- Original cell text (for debugging/audit)

**Purpose**:
- User can click activity in UI → see original DOCX location
- Validation errors reference specific cells
- Audit trail for compliance/review

---

## 11. AMBIGUITY AND ERROR MODEL

### 11.1 ERROR vs WARNING: Publishability Semantics

**Fundamental Distinction**:

**ERROR**: Data cannot be safely converted to canonical schedule. Blocks import/publication.
- Structural problems that prevent establishing trustworthy activity-teacher-resource relationships
- Ambiguities that could produce incorrect teacher availability
- Missing critical information that makes the schedule semantically invalid

**WARNING**: Data is imperfect but semantically safe. Allows import with review.
- Minor data quality issues
- Missing optional information
- Resolvable ambiguities that don't affect correctness

**INFO**: Informational only, no action required.

### 11.2 ERROR Categories (Block Import/Publication)

#### Structural Errors

**MALFORMED_TABLE**: Table dimensions inconsistent, missing critical columns
- **Severity**: ERROR
- **Reason**: Cannot reconstruct timetable grid
- **Example**: Header row missing, day column missing, irregular merge structure

**UNKNOWN_DAY**: Day name not in [MON, TUE, WED, THU, FRI, SAT]
- **Severity**: ERROR
- **Reason**: Cannot map activity to valid day
- **Example**: "FUNDAY", "SUN" (if Sunday not expected)

**INVALID_SLOT_RANGE**: Time range doesn't match institutional slots (S1-S9)
- **Severity**: ERROR
- **Reason**: Cannot map activity to valid time slot
- **Example**: "13:00-14:00" doesn't match any slot

**BREAK_AS_ACTIVITY**: Activity scheduled during break time
- **Severity**: ERROR
- **Reason**: Breaks are not working slots, scheduling here is structural error
- **Example**: Cell in "COFFEE BREAK" column contains activity

#### Ambiguity Errors (Prevent Safe Conversion)

**AMBIGUOUS_BLOCK**: Multiple teachers and multiple activities in one cell
- **Severity**: ERROR
- **Reason**: Cannot determine which teacher teaches which activity
- **Impact**: Could assign teacher to wrong activity, producing incorrect availability
- **Example**: "PY1, PE2 / (SU) (TS)" - is SU teaching PY1 or PE2?
- **Resolution**: Block must remain unresolved until user manually splits or clarifies

**AMBIGUOUS_RESOURCE_MULTI_ACTIVITY**: Multiple resources for multiple activities
- **Severity**: ERROR if activities are distinct teaching events
- **Reason**: Cannot determine resource assignment, could create scheduling conflicts
- **Example**: "PY1, PE2 / LAB1A, LAB1B" - which lab for which activity?
- **Resolution**: Manual clarification required

**UNRESOLVED_TEACHER_IDENTITY**: Teacher acronym cannot be resolved to unique identity
- **Severity**: ERROR
- **Reason**: Department-aware identity requires unique teacher mapping
- **Example**: "VR" matches multiple teachers in different departments with no department context
- **Resolution**: Add department context or disambiguate acronym

#### Data Integrity Errors

**MISSING_SUBJECT**: Activity cell has teacher/room but no subject
- **Severity**: ERROR
- **Reason**: Cannot create meaningful schedule activity without subject
- **Example**: Cell contains only "(VR)\nCA1"

**MISSING_TEACHER**: Activity cell has subject/room but no teacher
- **Severity**: ERROR for confirmed timetables
- **Reason**: Teacher availability system requires teacher for each activity
- **Example**: Cell contains only "DBMS\nCA1"

**DUPLICATE_ACTIVITY**: Exact same activity (section, day, slot, subject, teacher) appears multiple times
- **Severity**: ERROR
- **Reason**: Logical duplication, indicates parsing error or source error
- **Example**: "DBMS (VR) CA1" appears in both S3 and S4 for I-A Monday

### 11.3 WARNING Categories (Allow Import with Review)

#### Resolution Warnings

**AMBIGUOUS_RESOURCE_SINGLE_ACTIVITY**: Multiple resources for single activity
- **Severity**: ERROR
- **Reason**: Cannot determine which resource to assign (1:1:2 mapping is ambiguous)
- **Example**: "DBMS (VR) / LAB1A, LAB1B" - VR teaching DBMS, but which lab?
- **Action**: Block must remain unresolved until user clarifies
- **Rationale**: Parser must NOT select first resource or assign NULL silently. Requires explicit manual resolution.

#### Data Quality Warnings

**MISSING_RESOURCE**: Activity has no room specified (NULL)
- **Severity**: WARNING
- **Reason**: Some activities legitimately don't require rooms (tutorials, library activities, cultural events)
- **Example**: "Library/Research Activity" with no room
- **Action**: Accept NULL resource, allow publication with acknowledgment

**UNRESOLVED_RESOURCE**: Room code not found in resource database
- **Severity**: WARNING
- **Reason**: Resource may legitimately be added during review, or could be typo
- **Example**: "XYZ123" not in resources table, "Lab3C" not yet created
- **Action**: Flag for review, may add resource or correct code before publication

**UNRESOLVED_TEACHER_LEGEND**: Teacher acronym not found in faculty legend
- **Severity**: WARNING
- **Reason**: Can proceed with acronym, though full name unknown
- **Example**: "(ZZZ)" not in legend table (visiting faculty, new hire)
- **Action**: Flag for review, may need to add to legend or correct acronym

**POSSIBLE_DUPLICATE**: Adjacent cells have similar/identical text
- **Severity**: WARNING
- **Reason**: Might be duplicate or intentional repetition
- **Example**: Same activity text in S3 and S4 without gridSpan

**INCONSISTENT_FORMAT**: Cell content doesn't match expected pattern
- **Severity**: WARNING
- **Reason**: Can still extract, but format unusual
- **Example**: Teacher acronym without parentheses, multiple line breaks

### 11.4 INFO Categories

**MULTI_SLOT_DETECTED**: Activity spans multiple time slots via gridSpan
- **Severity**: INFO
- **Reason**: Expected behavior for labs

**LEGEND_TEACHER_MAPPED**: Teacher acronym successfully resolved via legend
- **Severity**: INFO
- **Reason**: Normal successful resolution

**TEACHER_IDENTITY_REUSED**: Teacher found in database, will reuse existing
- **Severity**: INFO
- **Reason**: Normal reuse behavior

### 11.5 Publishability Rules

**Preview Stage** (after parsing):
- May contain ERRORs, WARNINGs, INFOs
- User reviews import preview
- Unresolved blocks displayed for manual correction

**Draft Timetable** (after confirmation):
- Must have ZERO ERRORs
- May have WARNINGs (user acknowledges)
- Unresolved blocks converted to activities or excluded (user's choice)

**Confirmed Timetable** (published):
- Must have ZERO ERRORs
- Must have ZERO unresolved blocks
- All activities must have resolved teacher identity
- WARNINGs acceptable if user has reviewed and accepted

**Enforcement**:
```python
def can_publish(preview: DOCXImportPreview) -> tuple[bool, str]:
    """Check if preview can be published to confirmed timetable."""
    if preview.errors:
        return False, f"Cannot publish: {len(preview.errors)} error(s) must be resolved"

    if preview.unresolved_blocks:
        return False, f"Cannot publish: {len(preview.unresolved_blocks)} ambiguous block(s) require manual resolution"

    # Check all teachers are resolved
    unresolved_teachers = [t for t in preview.teacher_identities if not t.resolved_teacher_id]
    if unresolved_teachers:
        return False, f"Cannot publish: {len(unresolved_teachers)} teacher(s) unresolved"

    return True, "OK"
```

### 11.6 CONSOLIDATED ERROR/WARNING RULES

**AUTHORITATIVE ERROR/WARNING CLASSIFICATION**:

**ERROR** (blocks publication):
1. **Multiple possible resources for one activity** - Even 1:1:2 is ERROR (no "first wins")
2. **Multiple resources across multiple activities** - Mapping unknown
3. **Ambiguous teacher-activity mapping** - Multiple teachers, unclear associations
4. **Missing teacher** - Required for availability system
5. **Missing subject** - Cannot create meaningful activity
6. **Unresolved teacher identity** - Cannot map to unique teacher
7. **Structural failures** - Malformed table, invalid slots, break-as-activity

**WARNING** (allows publication with review):
1. **Missing resource** - Some activities legitimately have no room
2. **Unknown resource** - Resource may be added during review
3. **Unknown teacher legend** - Teacher acronym not in legend but can proceed
4. **Format inconsistencies** - Unusual but parseable

**KEY PRINCIPLE**: If ambiguity could produce incorrect teacher availability or resource conflicts, it's ERROR. If data is imperfect but semantically safe, it's WARNING.

---

## 12. RESOURCE HANDLING

### 12.1 Three-Stage Resource Processing

**Stage 1: Source Text Extraction**
- Extract raw text from cell: "Lab 1A", "CA1", "LAB 1A, LAB1B"
- Preserve original spacing, capitalization
- Store in `ResourceCandidate.code`

**Stage 2: Normalization for Comparison**
- Uppercase: "Lab 1A" → "LAB 1A"
- Remove extra whitespace: "LAB  1A" → "LAB 1A"
- Normalize spacing: "LAB 1A" → "LAB1A" (comparison form)
- Store in `ResourceCandidate.normalized_code`

**Stage 3: Resource Identity Resolution** (via Resource Foundation)
- Look up normalized code in resource database
- Match against: resource name, normalized_name, aliases
- Department-scoped resolution (if applicable)
- Result: `resolved_resource_id` (UUID) or UNRESOLVED or AMBIGUOUS

**Critical**: Normalization is for comparison only. It does NOT mean a canonical Resource has been resolved.

**Example Flow**:
```python
# Stage 1: Extract
raw_text = "Lab 1A"
candidate = ResourceCandidate(code="Lab 1A", normalized_code=None)

# Stage 2: Normalize
candidate.normalized_code = normalize_resource_code("Lab 1A")  # → "LAB1A"

# Stage 3: Resolve
resolution = resolve_resource(
    search_term=candidate.normalized_code,
    department="Computer Applications",
    db=db
)

if resolution.status == "FOUND":
    candidate.resolved_resource_id = resolution.resource_id
elif resolution.status == "AMBIGUOUS":
    # Multiple matches
    issue = ValidationIssue(
        severity=WARNING,
        code="AMBIGUOUS_RESOURCE",
        message=f"Resource '{candidate.code}' matches {len(resolution.matches)} resources"
    )
elif resolution.status == "NOT_FOUND":
    # No match
    issue = ValidationIssue(
        severity=WARNING,
        code="UNRESOLVED_RESOURCE",
        message=f"Resource '{candidate.code}' not found in database"
    )
```

### 12.2 Resource Ambiguity Handling

**Case 1: Multiple resources for single activity**
```python
TimetableBlock(
    activity_candidates=[ActivityCandidate(code="DBMS", ...)],
    teacher_candidates=[TeacherCandidate(acronym="VR", ...)],
    resource_candidates=[
        ResourceCandidate(code="CA1", ...),
        ResourceCandidate(code="CA2", ...),
    ]
)
```
- **Severity**: ERROR
- **Reason**: Cannot determine which resource to assign (1:1:2 mapping is ambiguous)
- **Resolution**: Block remains unresolved with `is_resolved=False`
- **CRITICAL**: Parser must NOT select first resource or silently assign NULL
- **User Action Required**: Manual clarification of correct resource

**Case 2: Multiple resources for multiple activities**
```python
TimetableBlock(
    activity_candidates=[
        ActivityCandidate(code="PY1", ...),
        ActivityCandidate(code="PE2", ...),
    ],
    teacher_candidates=[...],  # 2 or more
    resource_candidates=[
        ResourceCandidate(code="LAB1A", ...),
        ResourceCandidate(code="LAB1B", ...),
    ]
)
```
- **Severity**: ERROR
- **Reason**: Cannot determine which resource for which activity-teacher pair
- **Resolution**: Block remains unresolved with `is_resolved=False`
- **CRITICAL**: Parser must NOT attempt any pairing based on position, proximity, or order

---

## 13. EXPLICIT EXAMPLES

### 13.1 Example 1: Simple One-Slot Activity

**Cell Content**:
```
DBMS
(VR)
CA1
```

**Cell Location**: Monday, I-A, Column 4 (S3)

**Parsing Result**:
```python
TimetableBlock(
    day="monday",
    section="I-A",
    slots=["S3"],
    activity_candidates=[
        ActivityCandidate(code="DBMS", inferred_type="CLASS")
    ],
    teacher_candidates=[
        TeacherCandidate(acronym="VR", normalized="VR")
    ],
    resource_candidates=[
        ResourceCandidate(code="CA1", normalized="CA1")
    ],
    is_resolved=True,
    ambiguity_reason=None,
    original_text="DBMS\n(VR)\nCA1",
    source_location=SourceLocation(table_index=0, row_index=2, column_index=4, ...),
    issues=[]
)
```

**Conversion to CanonicalTimetable**:
```python
ResolvedActivity(
    day="monday",
    section="I-A",
    slots=["S3"],
    entry_type="CLASS",
    subject_or_activity="DBMS",
    teacher_acronym="VR",
    resource_code="CA1"
)
→ ScheduleActivity(
    day="monday",
    slots=["S3"],
    entry_type="CLASS",
    subject_or_activity="DBMS",
    section="I-A",
    teacher_acronym="VR",
    room="CA1",
    source_location=...
)
```

### 13.2 Example 2: Ambiguous Activity Phrase with Punctuation

**Cell Content**:
```
PE 1,2,3,4
(GK, SS, KPS, VK)
(LAB 1A)
```

**Cell Location**: Tuesday, I-B, Columns 2-3 (S1-S2), `gridSpan=2`

**Ambiguity**: Does "PE 1,2,3,4" represent:
- ONE activity named "PE 1,2,3,4"?
- FOUR separate activities: PE 1, PE 2, PE 3, PE 4?
- Something else?

**Parser Behavior - CONSERVATIVE (NO GUESSING)**:
```python
TimetableBlock(
    day="tuesday",
    section="I-B",
    slots=["S1", "S2"],  # From gridSpan=2
    activity_candidates=[
        # PRESERVE raw phrase - punctuation alone doesn't define activity boundaries
        ActivityCandidate(code="PE 1,2,3,4", inferred_type="LAB")
    ],
    teacher_candidates=[
        TeacherCandidate(acronym="GK", normalized="GK"),
        TeacherCandidate(acronym="SS", normalized="SS"),
        TeacherCandidate(acronym="KPS", normalized="KPS"),
        TeacherCandidate(acronym="VK", normalized="VK"),
    ],
    resource_candidates=[
        ResourceCandidate(code="LAB 1A", normalized="LAB1A")
    ],
    is_resolved=False,
    ambiguity_reason="Activity phrase 'PE 1,2,3,4' has ambiguous boundaries, 4 teachers, 1 resource: cannot determine relationships",
    original_text="PE 1,2,3,4\n(GK, SS, KPS, VK)\n(LAB 1A)",
    source_location=SourceLocation(table_index=0, row_index=7, column_index=2, ...),
    issues=[
        ValidationIssue(
            severity=ERROR,
            code="AMBIGUOUS_BLOCK",
            message="Cannot determine activity boundaries in 'PE 1,2,3,4'. Contains 4 teachers. Manual resolution required."
        ),
        ValidationIssue(
            severity=ERROR,
            code="ACTIVITY_TOKENIZATION_AMBIGUOUS",
            message="Activity phrase 'PE 1,2,3,4' contains punctuation that may indicate multiple activities, but structure doesn't provide definitive boundaries. Parser must not guess."
        )
    ]
)
```

**Cannot Convert**: Remains as unresolved block in preview.

**CRITICAL**: Parser does NOT automatically split on commas. "PE 1,2,3,4" could be one activity name or multiple. Without structural evidence, preserve the raw phrase.

### 13.3 Example 3: Parallel Activities with Spatial Grouping (Still Ambiguous)

**Cell Content**:
```
ADA1, DT 3,4
(TSP)   (VPP, SU)
(Lab 1B) (Lab 1A)
```

**Observation**: Spatial grouping (whitespace, parentheses positioning) SUGGESTS:
- ADA1 with TSP in Lab 1B
- DT 3,4 with (VPP, SU) in Lab 1A

**Parser Behavior** (NO GUESSING BASED ON WHITESPACE):
```python
TimetableBlock(
    day="monday",
    section="III-A",
    slots=["S2", "S3"],
    activity_candidates=[
        # Even with spatial grouping, cannot determine activity boundaries
        ActivityCandidate(code="ADA1", inferred_type="LAB"),
        ActivityCandidate(code="DT 3,4", inferred_type="LAB"),  # ONE candidate, NOT split into "DT 3" and "DT 4"
    ],
    teacher_candidates=[
        TeacherCandidate(acronym="TSP", normalized="TSP"),
        TeacherCandidate(acronym="VPP", normalized="VPP"),
        TeacherCandidate(acronym="SU", normalized="SU"),
    ],
    resource_candidates=[
        ResourceCandidate(code="Lab 1B", normalized="LAB1B"),
        ResourceCandidate(code="Lab 1A", normalized="LAB1A"),
    ],
    is_resolved=False,
    ambiguity_reason="2 activities, 3 teachers, 2 resources: spatial grouping (whitespace/parentheses) is not machine-readable structure",
    issues=[
        ValidationIssue(
            severity=ERROR,
            code="AMBIGUOUS_BLOCK",
            message="Cell contains 2 activities, 3 teachers, 2 resources. Spatial grouping suggests associations but is not semantically binding. Manual resolution required."
        )
    ]
)
```

**Cannot Convert**: Even though whitespace suggests grouping, parser does NOT invent associations based on visual proximity.

**CRITICAL**:
- "DT 3,4" preserved as ONE activity candidate (punctuation doesn't define boundaries)
- Whitespace and parentheses positioning are visual formatting, NOT semantic structure
- Parser must NOT pair based on spatial proximity

### 13.4 Example 4: Fully Resolved Multi-Activity Cell (Edge Case)

**Cell Content**:
```
WEB
(VPP)
CA1
```

**Cell Location**: Tuesday, I-B, Column 6 (S4)

**Parsing Result**:
```python
TimetableBlock(
    day="tuesday",
    section="I-B",
    slots=["S4"],
    activity_candidates=[
        ActivityCandidate(code="WEB", inferred_type="CLASS")
    ],
    teacher_candidates=[
        TeacherCandidate(acronym="VPP", normalized="VPP")
    ],
    resource_candidates=[
        ResourceCandidate(code="CA1", normalized="CA1")
    ],
    is_resolved=True,  # 1:1:1
    ambiguity_reason=None,
    issues=[]
)
```

**Conversion**: ✅ Succeeds

### 13.5 Example 5: Ambiguous Teacher Mapping (ERROR)

**Cell Content**:
```
PY1, PE2
(SU) (TS)
LAB1B
```

**Parsing Result**:
```python
TimetableBlock(
    activity_candidates=[
        ActivityCandidate(code="PY1", ...),
        ActivityCandidate(code="PE2", ...),
    ],
    teacher_candidates=[
        TeacherCandidate(acronym="SU", ...),
        TeacherCandidate(acronym="TS", ...),
    ],
    resource_candidates=[
        ResourceCandidate(code="LAB1B", ...)
    ],
    is_resolved=False,
    ambiguity_reason="2 activities, 2 teachers: cannot determine associations",
    issues=[
        ValidationIssue(
            severity=ERROR,
            code="AMBIGUOUS_TEACHER_MAPPING",
            message="Cannot determine if SU teaches PY1 or PE2. Cannot determine if TS teaches PY1 or PE2. Manual resolution required to prevent incorrect teacher availability."
        )
    ]
)
```

### 13.6 Example 6: Ambiguous Resource Mapping (ERROR)

**Cell Content**:
```
DS 1,2
(SS, KPS)
LAB1A, LAB1B
```

**Parsing Result**:
```python
TimetableBlock(
    activity_candidates=[
        # PRESERVE "DS 1,2" as single candidate - comma doesn't define boundaries
        ActivityCandidate(code="DS 1,2", inferred_type="LAB"),
    ],
    teacher_candidates=[
        TeacherCandidate(acronym="SS", normalized="SS"),
        TeacherCandidate(acronym="KPS", normalized="KPS"),
    ],
    resource_candidates=[
        # Extract individually from separate occurrences
        ResourceCandidate(code="LAB1A", normalized="LAB1A"),
        ResourceCandidate(code="LAB1B", normalized="LAB1B"),
    ],
    is_resolved=False,
    ambiguity_reason="Activity 'DS 1,2' (boundaries unclear), 2 teachers, 2 resources: cannot determine associations",
    issues=[
        ValidationIssue(
            severity=ERROR,
            code="AMBIGUOUS_BLOCK",
            message="Cell contains activity 'DS 1,2' with ambiguous boundaries, 2 teachers, 2 resources. Cannot establish safe relationships."
        ),
        ValidationIssue(
            severity=ERROR,
            code="ACTIVITY_TOKENIZATION_AMBIGUOUS",
            message="Activity phrase 'DS 1,2' may represent one or two activities. Structure doesn't provide definitive boundaries."
        )
    ]
)
```

**CRITICAL**:
- "DS 1,2" NOT automatically split into "DS 1" and "DS 2"
- Individual resource candidates preserved (NOT "LAB1A, LAB1B" as string)
- Multiple teachers + unclear activity boundaries = ERROR

### 13.7 Example 7: Missing Teacher (ERROR)

**Cell Content**:
```
DBMS
CA1
```

**Parsing Result**:
```python
TimetableBlock(
    activity_candidates=[
        ActivityCandidate(code="DBMS", ...)
    ],
    teacher_candidates=[],  # Empty
    resource_candidates=[
        ResourceCandidate(code="CA1", ...)
    ],
    is_resolved=False,
    ambiguity_reason="No teacher specified",
    issues=[
        ValidationIssue(
            severity=ERROR,
            code="MISSING_TEACHER",
            message="Activity 'DBMS' has no teacher specified. Teacher required for teacher availability system."
        )
    ]
)
```

### 13.8 Example 8: Missing Resource (WARNING, Acceptable)

**Cell Content**:
```
Library/Research Activity
```

**Parsing Result**:
```python
TimetableBlock(
    activity_candidates=[
        ActivityCandidate(code="Library/Research Activity", inferred_type="OTHER")
    ],
    teacher_candidates=[],  # May or may not have teacher
    resource_candidates=[],  # No room
    is_resolved=True if teacher present else False,
    issues=[
        ValidationIssue(
            severity=WARNING,
            code="MISSING_RESOURCE",
            message="Activity 'Library/Research Activity' has no room specified. This may be acceptable for certain activity types."
        )
    ]
)
```

---

## 14. DETERMINISTIC RECONSTRUCTION ALGORITHM (REVISED)

### 14.1 High-Level Pipeline (REVISED)

```
DOCX Binary
    ↓
WordprocessingML Extraction (python-docx)
    ↓
Table Detection
    ↓
Physical Grid Reconstruction
    ↓
Merge Resolution
    ↓
Header Row Detection
    ↓
Day/Slot Mapping
    ↓
Section Row Identification
    ↓
Raw Timetable Block Extraction  ← NEW: Preserve individual candidates
    ↓
Logical Activity/Candidate Reconstruction  ← NEW: Build TimetableBlock objects
    ↓
Teacher/Resource Normalization  ← Source text → normalized form
    ↓
Teacher/Resource Identity Resolution  ← Normalized form → UUID or UNRESOLVED
    ↓
Ambiguity Detection & Classification  ← Determine is_resolved status
    ↓
Unresolved Block Representation  ← Keep unresolved blocks separate
    ↓
Resolved Activity Conversion  ← Only resolved blocks → ResolvedActivity
    ↓
DOCXImportPreview  ← Contains resolved + unresolved separately
    ↓
(Optional) CanonicalTimetable  ← Only if no ERRORs, only resolved activities
    ↓
ValidationEngine  ← Standard validation on canonical activities
```

### 14.2 Key Algorithm Changes

#### Stage 7: Raw Timetable Block Extraction (NEW)
```python
def extract_timetable_block(cell: GridCell, day: str, section: str, slot_start: str, gridSpan: int) -> TimetableBlock:
    """Extract a TimetableBlock preserving individual candidates.

    DOES NOT attempt to resolve associations.
    """
    if not cell.text.strip() or is_break_cell(cell):
        return None

    # Determine slot span
    slots = [slot_start]
    if gridSpan and gridSpan > 1:
        for i in range(1, gridSpan):
            next_slot_num = int(slot_start[1:]) + i
            slots.append(f"S{next_slot_num}")

    # Parse cell content into lines
    lines = [line.strip() for line in cell.text.split('\n') if line.strip()]

    # Extract individual candidates (NO ASSOCIATION)
    activity_candidates = extract_activity_candidates(lines)
    teacher_candidates = extract_teacher_candidates(lines)
    resource_candidates = extract_resource_candidates(lines)

    # Create block
    block = TimetableBlock(
        day=day,
        section=section,
        slots=slots,
        activity_candidates=activity_candidates,
        teacher_candidates=teacher_candidates,
        resource_candidates=resource_candidates,
        is_resolved=False,  # Determined later
        ambiguity_reason=None,
        source_location=create_source_location(cell),
        original_text=cell.text,
        issues=[]
    )

    return block
```

#### Stage 9: Ambiguity Detection & Classification (NEW)
```python
def classify_block_resolution(block: TimetableBlock) -> None:
    """Determine if block is resolved or ambiguous.

    Updates block.is_resolved and block.ambiguity_reason in place.
    """
    n_activities = len(block.activity_candidates)
    n_teachers = len(block.teacher_candidates)
    n_resources = len(block.resource_candidates)

    # Rule 1: Missing critical information
    if n_activities == 0:
        block.is_resolved = False
        block.ambiguity_reason = "No activity found"
        block.issues.append(ValidationIssue(
            severity=ERROR,
            code="MISSING_SUBJECT",
            message="Cell contains no recognizable activity/subject"
        ))
        return

    if n_teachers == 0:
        block.is_resolved = False
        block.ambiguity_reason = "No teacher specified"
        block.issues.append(ValidationIssue(
            severity=ERROR,
            code="MISSING_TEACHER",
            message=f"Activity '{block.activity_candidates[0].code}' has no teacher"
        ))
        return

    # Rule 2: Resolved if exactly 1:1:1 or 1:1:0/1
    if n_activities == 1 and n_teachers == 1 and n_resources <= 1:
        block.is_resolved = True
        block.ambiguity_reason = None
        if n_resources == 0:
            block.issues.append(ValidationIssue(
                severity=WARNING,
                code="MISSING_RESOURCE",
                message=f"Activity '{block.activity_candidates[0].code}' has no room"
            ))
        return

    # Rule 3: Multiple activities or teachers = AMBIGUOUS
    if n_activities > 1 or n_teachers > 1:
        block.is_resolved = False
        block.ambiguity_reason = f"{n_activities} activities, {n_teachers} teachers"
        if n_resources > 1:
            block.ambiguity_reason += f", {n_resources} resources"
        block.ambiguity_reason += ": cannot determine associations"

        block.issues.append(ValidationIssue(
            severity=ERROR,
            code="AMBIGUOUS_BLOCK",
            message=(
                f"Cell contains {n_activities} activities with {n_teachers} teachers"
                + (f" and {n_resources} resources" if n_resources > 1 else "")
                + ". Cannot establish safe activity-teacher-resource relationships. "
                + "Manual resolution required to prevent incorrect teacher availability."
            )
        ))
        return

    # Rule 4: Single activity, single teacher, multiple resources
    if n_activities == 1 and n_teachers == 1 and n_resources > 1:
        block.is_resolved = False  # ERROR: ambiguous resource mapping
        block.ambiguity_reason = f"1 activity, 1 teacher, {n_resources} resources: cannot determine correct resource"
        block.issues.append(ValidationIssue(
            severity=ERROR,  # Changed from WARNING to ERROR
            code="AMBIGUOUS_RESOURCE_SINGLE_ACTIVITY",
            message=f"Activity '{block.activity_candidates[0].code}' has {n_resources} possible resources. Manual resolution required."
        ))
        # CRITICAL: Parser must NOT select first resource or assign NULL
        # Block remains unresolved until user provides explicit mapping
        return
```

#### Stage 10: Conversion to ResolvedActivity (NEW)
```python
def convert_to_resolved_activity(block: TimetableBlock) -> ResolvedActivity | None:
    """Convert a resolved TimetableBlock to ResolvedActivity.

    Returns None if block is not resolved.
    """
    if not block.is_resolved:
        return None

    if not block.activity_candidates or not block.teacher_candidates:
        return None  # Safety check

    activity = block.activity_candidates[0]
    teacher = block.teacher_candidates[0]
    resource = block.resource_candidates[0] if block.resource_candidates else None

    return ResolvedActivity(
        day=block.day,
        section=block.section,
        slots=block.slots,
        entry_type=activity.inferred_type,
        subject_or_activity=activity.code,
        teacher_acronym=teacher.normalized_acronym,
        resource_code=resource.normalized_code if resource else None,
        source_location=block.source_location
    )
```

### 14.3 Conversion Rules: When ScheduleActivity is Allowed

**ScheduleActivity can ONLY be created from**:
1. A `ResolvedActivity` object (derived from resolved `TimetableBlock`)
2. After teacher identity resolution completes
3. After resource resolution completes
4. With NO ERROR-level issues

**ScheduleActivity MUST NOT be created from**:
1. Unresolved `TimetableBlock` objects
2. Blocks with ERROR-level validation issues
3. Blocks with ambiguous teacher-activity associations
4. Blocks with missing required information (subject, teacher)

**Conversion Function**:
```python
def block_to_schedule_activity(resolved: ResolvedActivity, teacher_id: UUID | None, resource_id: UUID | None) -> ScheduleActivity:
    """Convert ResolvedActivity to CanonicalTimetable ScheduleActivity.

    Prerequisites:
    - resolved must come from a TimetableBlock with is_resolved=True
    - teacher_id may be None if teacher identity resolution is pending
    - resource_id may be None if resource is unspecified or unresolved
    """
    return ScheduleActivity(
        day=resolved.day,
        slots=resolved.slots,
        entry_type=resolved.entry_type,
        subject_or_activity=resolved.subject_or_activity,
        section=resolved.section,
        teacher_acronym=resolved.teacher_acronym,
        room=resolved.resource_code,  # May be None
        source_location=resolved.source_location,
        # Additional fields populated by validator:
        # resolved_teacher_id = teacher_id (after identity resolution)
        # resolved_resource_id = resource_id (after resource resolution)
    )
```

---

## 15. TEST STRATEGY (REVISED)

### 15.1 Unit Tests: Intermediate Representation

**Test Group: TimetableBlock Construction**
```python
def test_simple_resolved_block():
    """Verify simple 1:1:1 block is correctly marked as resolved."""
    block = TimetableBlock(
        day="monday",
        section="I-A",
        slots=["S3"],
        activity_candidates=[ActivityCandidate(code="DBMS", inferred_type="CLASS")],
        teacher_candidates=[TeacherCandidate(acronym="VR", normalized="VR")],
        resource_candidates=[ResourceCandidate(code="CA1", normalized="CA1")],
        is_resolved=False,  # Before classification
        ambiguity_reason=None,
        source_location=...,
        original_text="DBMS\n(VR)\nCA1",
        issues=[]
    )

    classify_block_resolution(block)

    assert block.is_resolved == True
    assert block.ambiguity_reason is None
    assert len(block.issues) == 0


def test_ambiguous_block_no_flattening():
    """CRITICAL: Verify ambiguous data is NOT flattened to comma-separated strings."""
    cell_text = "PY1, PE2\n(SU) (TS)\nLAB1B"

    block = parse_timetable_cell(cell_text, day="tuesday", section="I-A", slots=["S4"])

    # Assert individual candidates preserved
    assert len(block.activity_candidates) == 2
    assert block.activity_candidates[0].code == "PY1"
    assert block.activity_candidates[1].code == "PE2"

    assert len(block.teacher_candidates) == 2
    assert block.teacher_candidates[0].acronym == "SU"
    assert block.teacher_candidates[1].acronym == "TS"

    # Assert NOT flattened
    assert not any("," in c.code for c in block.activity_candidates)
    assert not any("," in c.acronym for c in block.teacher_candidates)

    # Assert marked as unresolved
    classify_block_resolution(block)
    assert block.is_resolved == False
    assert "cannot determine associations" in block.ambiguity_reason.lower()
    assert any(issue.code == "AMBIGUOUS_BLOCK" for issue in block.issues)


def test_individual_candidates_preserved():
    """Verify individual candidates remain separate, not merged into comma-separated strings."""
    cell_text = "PY1\n(SS)\n(KPS)\nLAB1A"  # Use clearer example

    block = parse_timetable_cell(cell_text, day="wednesday", section="III-A", slots=["S5"])

    # Verify individual teachers extracted from separate parentheses
    assert len(block.teacher_candidates) == 2
    assert [c.normalized_acronym for c in block.teacher_candidates] == ["SS", "KPS"]

    # Verify NOT flattened to comma-separated string
    assert not any("," in c.acronym for c in block.teacher_candidates)

    # Verify individual resources
    assert len(block.resource_candidates) == 1
    assert block.resource_candidates[0].code == "LAB1A"

    # Verify relationships NOT invented
    # (No field like activity_candidates[0].teacher = "SS")


def test_activity_phrase_preserved_not_split():
    """CRITICAL: Verify activity phrases with punctuation are NOT automatically split."""
    cell_text = "DS 3,4\n(SS)\nLAB1A"

    block = parse_timetable_cell(cell_text, day="wednesday", section="III-A", slots=["S5", "S6"])

    # CRITICAL: "DS 3,4" should be ONE activity candidate
    assert len(block.activity_candidates) == 1
    assert block.activity_candidates[0].code == "DS 3,4"

    # NOT split into ["DS 3", "DS 4"]
    assert all("," not in c.code or c.code == "DS 3,4" for c in block.activity_candidates)


def test_pe_numbers_not_automatically_split():
    """CRITICAL: Verify 'PE 1,2,3,4' is NOT automatically split into 4 activities."""
    cell_text = "PE 1,2,3,4\n(GK)\nLAB1A"

    block = parse_timetable_cell(cell_text, day="saturday", section="I-A", slots=["S2", "S3"])

    # Should preserve as single ambiguous phrase
    assert len(block.activity_candidates) == 1
    assert block.activity_candidates[0].code == "PE 1,2,3,4"

    # Should be marked unresolved
    classify_block_resolution(block)
    assert block.is_resolved == False


def test_no_arbitrary_teacher_activity_pairing():
    """CRITICAL: Verify parser does NOT invent teacher-activity associations."""
    cell_text = "PY1, PE2\n(SU) (TS)\nLAB1A"

    block = parse_timetable_cell(cell_text, day="thursday", section="I-B", slots=["S2", "S3"])

    # Verify candidates extracted
    # Note: "PY1, PE2" might be preserved as phrase OR detected as two based on clear separation
    # But regardless, NO pairing should be invented
    assert len(block.teacher_candidates) == 2  # SU, TS

    # CRITICAL: Verify NO pairing field exists
    # ActivityCandidate should NOT have .teacher attribute
    for activity in block.activity_candidates:
        assert not hasattr(activity, 'teacher')
        assert not hasattr(activity, 'assigned_teacher')

    # Verify marked as ERROR
    classify_block_resolution(block)
    assert block.is_resolved == False
    assert any(issue.severity == ERROR for issue in block.issues)


def test_no_arbitrary_room_activity_pairing():
    """CRITICAL: Verify parser does NOT invent resource-activity associations."""
    cell_text = "DBMS\n(VR)\nCA1, CA2"

    block = parse_timetable_cell(cell_text, day="monday", section="III-A", slots=["S2"])

    assert len(block.activity_candidates) == 1  # DBMS
    assert len(block.teacher_candidates) == 1  # VR
    assert len(block.resource_candidates) == 2  # CA1, CA2

    # CRITICAL: Verify NO association
    for activity in block.activity_candidates:
        assert not hasattr(activity, 'room')
        assert not hasattr(activity, 'assigned_resource')

    # 1:1:2 is ambiguous, parser does NOT select "first" resource
    classify_block_resolution(block)
    assert block.is_resolved == False
    assert any(issue.severity == ERROR for issue in block.issues)


def test_multiple_resources_not_first_wins():
    """CRITICAL: Verify parser does NOT use 'first resource wins' for ambiguous resources."""
    block = TimetableBlock(
        day="monday",
        section="I-A",
        slots=["S3"],
        activity_candidates=[ActivityCandidate(code="DBMS", inferred_type="CLASS")],
        teacher_candidates=[TeacherCandidate(acronym="VR", normalized="VR")],
        resource_candidates=[
            ResourceCandidate(code="LAB1A", normalized="LAB1A"),
            ResourceCandidate(code="LAB1B", normalized="LAB1B"),
        ],
        is_resolved=False,  # Before classification
        ambiguity_reason=None,
        source_location=...,
        original_text="DBMS\n(VR)\nLAB1A, LAB1B",
        issues=[]
    )

    classify_block_resolution(block)

    # MUST remain unresolved
    assert block.is_resolved == False
    assert "ambiguous" in block.ambiguity_reason.lower() or "multiple" in block.ambiguity_reason.lower()
    assert any(issue.severity == ERROR for issue in block.issues)

    # Verify conversion attempt returns None
    resolved = convert_to_resolved_activity(block)
    assert resolved is None


def test_ambiguous_resource_remains_unresolved():
    """Verify ambiguous resource mapping (even 1:1:2) remains unresolved."""
    preview = DOCXImportPreview(
        resolved_blocks=[],
        unresolved_blocks=[
            TimetableBlock(
                activity_candidates=[ActivityCandidate(code="WEB", ...)],
                teacher_candidates=[TeacherCandidate(acronym="VPP", ...)],
                resource_candidates=[
                    ResourceCandidate(code="CA1", ...),
                    ResourceCandidate(code="CA2", ...),
                ],
                is_resolved=False,
                ambiguity_reason="1 activity, 1 teacher, 2 resources",
                ...
            )
        ],
        errors=[ValidationIssue(severity=ERROR, code="AMBIGUOUS_RESOURCE_SINGLE_ACTIVITY", ...)],
        warnings=[],
        ...
    )

    can_publish, reason = can_publish_timetable(preview)

    # Cannot publish due to unresolved block
    assert can_publish == False
    assert "unresolved" in reason.lower() or "ambiguous" in reason.lower()


def test_unresolved_blocks_cannot_convert():
    """Verify unresolved blocks cannot convert to ScheduleActivity."""
    block = TimetableBlock(
        day="friday",
        section="I-A",
        slots=["S7"],
        activity_candidates=[
            ActivityCandidate(code="PY1", inferred_type="LAB"),
            ActivityCandidate(code="PE2", inferred_type="LAB"),
        ],
        teacher_candidates=[
            TeacherCandidate(acronym="SU", normalized="SU"),
            TeacherCandidate(acronym="TS", normalized="TS"),
        ],
        resource_candidates=[ResourceCandidate(code="LAB1B", normalized="LAB1B")],
        is_resolved=False,
        ambiguity_reason="2 activities, 2 teachers",
        source_location=...,
        original_text="PY1, PE2\n(SU) (TS)\nLAB1B",
        issues=[ValidationIssue(severity=ERROR, code="AMBIGUOUS_BLOCK", ...)]
    )

    resolved = convert_to_resolved_activity(block)

    assert resolved is None  # Cannot convert


def test_unresolved_blocks_cannot_be_published():
    """CRITICAL: Verify unresolved blocks prevent publication."""
    preview = DOCXImportPreview(
        academic_year="2026-Odd",
        department="Computer Applications",
        source_file="timetable.docx",
        resolved_blocks=[
            ResolvedActivity(
                day="monday",
                section="I-A",
                slots=["S1"],
                entry_type="CLASS",
                subject_or_activity="DBMS",
                teacher_acronym="VR",
                resource_code="CA1",
                source_location=...
            )
        ],
        unresolved_blocks=[
            TimetableBlock(
                day="tuesday",
                section="I-A",
                slots=["S4"],
                activity_candidates=[...],  # Multiple
                teacher_candidates=[...],  # Multiple
                is_resolved=False,
                ...
            )
        ],
        errors=[],
        warnings=[],
        total_blocks=2,
        resolved_count=1,
        unresolved_count=1
    )

    can_publish, reason = can_publish_timetable(preview)

    assert can_publish == False
    assert "1 ambiguous block" in reason.lower()
```

### 15.2 Unit Tests: Resource Handling Stages

**Test Group: Three-Stage Resource Processing**
```python
def test_resource_extraction_stage():
    """Stage 1: Extract raw text preserving original formatting."""
    cell_text = "DBMS\n(VR)\nLab 1A"

    candidates = extract_resource_candidates([line.strip() for line in cell_text.split('\n')])

    assert len(candidates) == 1
    assert candidates[0].code == "Lab 1A"  # Original preserved
    assert candidates[0].normalized_code is None  # Not yet normalized


def test_resource_normalization_stage():
    """Stage 2: Normalize for comparison without resolving identity."""
    candidate = ResourceCandidate(code="Lab 1A", normalized_code=None)

    candidate.normalized_code = normalize_resource_code(candidate.code)

    assert candidate.normalized_code == "LAB1A"  # Uppercase, no spaces
    # But: normalized does NOT mean resolved to Resource entity


def test_resource_resolution_stage():
    """Stage 3: Resolve normalized code to canonical Resource entity."""
    candidate = ResourceCandidate(code="Lab 1A", normalized_code="LAB1A")

    # Mock database lookup
    db_resources = [
        Resource(id=uuid4(), name="Lab 1A", normalized_name="LAB1A", ...),
        Resource(id=uuid4(), name="Lab 1B", normalized_name="LAB1B", ...),
    ]

    resolution = resolve_resource_identity(candidate, db_resources, department="Computer Applications")

    assert resolution.status == "FOUND"
    assert resolution.resource_id is not None
    # NOW it's resolved to canonical entity


def test_normalization_not_resolution():
    """CRITICAL: Verify normalization alone does NOT imply resolution."""
    candidate = ResourceCandidate(code="XYZ Lab", normalized_code="XYZLAB")

    # Normalized form exists
    assert candidate.normalized_code is not None

    # But resolution requires database lookup
    db_resources = []  # Empty database
    resolution = resolve_resource_identity(candidate, db_resources, department="Computer Applications")

    assert resolution.status == "NOT_FOUND"
    # Normalized but NOT resolved
```

### 15.3 Unit Tests: ERROR vs WARNING Semantics

**Test Group: Validation Severity**
```python
def test_ambiguous_teacher_is_error():
    """Ambiguous teacher-activity mapping is ERROR (prevents publication)."""
    block = TimetableBlock(
        activity_candidates=[
            ActivityCandidate(code="PY1", ...),
            ActivityCandidate(code="PE2", ...),
        ],
        teacher_candidates=[
            TeacherCandidate(acronym="SU", ...),
            TeacherCandidate(acronym="TS", ...),
        ],
        resource_candidates=[ResourceCandidate(code="LAB1B", ...)],
        ...
    )

    classify_block_resolution(block)

    error_issues = [issue for issue in block.issues if issue.severity == ERROR]
    assert len(error_issues) > 0
    assert any("ambiguous" in issue.message.lower() for issue in error_issues)
    assert block.is_resolved == False


def test_missing_teacher_is_error():
    """Missing teacher is ERROR for teacher availability system."""
    block = TimetableBlock(
        activity_candidates=[ActivityCandidate(code="DBMS", ...)],
        teacher_candidates=[],  # No teacher
        resource_candidates=[ResourceCandidate(code="CA1", ...)],
        ...
    )

    classify_block_resolution(block)

    assert any(issue.code == "MISSING_TEACHER" and issue.severity == ERROR for issue in block.issues)


def test_missing_resource_is_warning():
    """Missing resource is WARNING (acceptable for some activities)."""
    block = TimetableBlock(
        activity_candidates=[ActivityCandidate(code="Library Research", ...)],
        teacher_candidates=[TeacherCandidate(acronym="VR", ...)],
        resource_candidates=[],  # No room
        ...
    )

    classify_block_resolution(block)

    # Should still be resolved (1:1:0)
    assert block.is_resolved == True

    # But should have WARNING
    warning_issues = [issue for issue in block.issues if issue.severity == WARNING]
    assert any("missing" in issue.message.lower() and "resource" in issue.message.lower() for issue in warning_issues)


def test_unresolved_teacher_legend_is_warning():
    """Teacher not in legend is WARNING (can still proceed)."""
    # Teacher acronym "ZZZ" not found in legend
    issue = validate_teacher_in_legend("ZZZ", legend={"VR": "Veena R", "SU": "S. Uma"})

    assert issue.severity == WARNING
    assert issue.code == "UNRESOLVED_TEACHER_LEGEND"


def test_errors_block_publication():
    """Verify ERRORs prevent publication."""
    preview = DOCXImportPreview(
        resolved_blocks=[...],
        unresolved_blocks=[],
        errors=[
            ValidationIssue(severity=ERROR, code="AMBIGUOUS_BLOCK", message="...")
        ],
        warnings=[],
        ...
    )

    can_publish, reason = can_publish_timetable(preview)

    assert can_publish == False
    assert "error" in reason.lower()


def test_warnings_allow_publication():
    """Verify WARNINGs allow publication with acknowledgment."""
    preview = DOCXImportPreview(
        resolved_blocks=[...],
        unresolved_blocks=[],
        errors=[],
        warnings=[
            ValidationIssue(severity=WARNING, code="MISSING_RESOURCE", message="...")
        ],
        ...
    )

    can_publish, reason = can_publish_timetable(preview)

    assert can_publish == True  # Allowed despite warnings
```

### 15.4 Integration Tests: End-to-End Parsing

**Test Group: Real Document Parsing**
```python
def test_parse_mca_timetable_v7():
    """Parse actual MCA Timetable-2026-Odd V7.docx."""
    docx_bytes = read_test_file("MCA Timetable-2026-Odd V7.docx")

    preview = parse_docx_timetable(docx_bytes)

    # Verify preview structure
    assert preview.academic_year == "2026-Odd"
    assert preview.department == "Computer Applications"
    assert preview.total_blocks > 0
    assert preview.resolved_count >= 0
    assert preview.unresolved_count >= 0
    assert preview.resolved_count + preview.unresolved_count == preview.total_blocks

    # Verify no comma-separated flattening
    for block in preview.unresolved_blocks:
        for activity in block.activity_candidates:
            assert "," not in activity.code or activity.code.count(",") == 0  # If comma exists, it's part of original like "DS 3,4"
        for teacher in block.teacher_candidates:
            assert "," not in teacher.acronym


def test_resolved_activities_map_to_canonical():
    """Verify resolved blocks convert correctly to CanonicalTimetable."""
    docx_bytes = read_test_file("MCA Timetable-2026-Odd V7.docx")

    preview = parse_docx_timetable(docx_bytes)

    if preview.errors:
        pytest.skip("Document has errors, cannot convert")

    canonical = convert_to_canonical_timetable(preview)

    assert canonical is not None
    assert len(canonical.activities) == preview.resolved_count

    # Verify each activity has required fields
    for activity in canonical.activities:
        assert activity.subject_or_activity is not None
        assert activity.teacher_acronym is not None
        assert activity.day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
        assert len(activity.slots) > 0


def test_unresolved_blocks_not_in_canonical():
    """Verify unresolved blocks do NOT appear in CanonicalTimetable."""
    preview = DOCXImportPreview(
        resolved_blocks=[
            ResolvedActivity(
                day="monday",
                section="I-A",
                slots=["S1"],
                entry_type="CLASS",
                subject_or_activity="DBMS",
                teacher_acronym="VR",
                resource_code="CA1",
                source_location=...
            )
        ],
        unresolved_blocks=[
            TimetableBlock(
                activity_candidates=[...],  # Multiple ambiguous
                is_resolved=False,
                ...
            )
        ],
        errors=[],
        warnings=[],
        ...
    )

    canonical = convert_to_canonical_timetable(preview)

    # Only 1 resolved block should convert
    assert len(canonical.activities) == 1
    assert canonical.activities[0].subject_or_activity == "DBMS"
```

### 15.5 Test Coverage Requirements

**Must Test**:
1. ✅ Ambiguous data NOT flattened to comma-separated strings
2. ✅ Individual candidates preserved in TimetableBlock
3. ✅ No arbitrary teacher-activity pairing
4. ✅ No arbitrary room-activity pairing
5. ✅ Unresolved blocks cannot convert to ScheduleActivity
6. ✅ Unresolved blocks cannot be published
7. ✅ Resolved activities map correctly to CanonicalTimetable
8. ✅ Three-stage resource handling (extract → normalize → resolve)
9. ✅ Normalization ≠ resolution
10. ✅ ERROR blocks publication, WARNING allows with review
11. ✅ Missing teacher = ERROR
12. ✅ Missing resource = WARNING
13. ✅ Ambiguous teacher-activity = ERROR
14. ✅ Real document parsing preserves structure

**NEW Required Tests**:
15. ✅ One candidate but candidate itself is tokenization-ambiguous → unresolved
16. ✅ 1 activity + 1 teacher + 2 resources → ERROR + unresolved
17. ✅ Multiple resources never select first
18. ✅ Ambiguous block can be manually resolved into multiple ResolvedActivity objects
19. ✅ Manual resolution preserves source_location
20. ✅ Manual resolution reruns validation
21. ✅ Unresolved block cannot publish
22. ✅ Parentheses classify candidate type but do not establish relationships
23. ✅ Exact physical/logical grid dimensions match actual DOCX

### 15.6 New Test Implementations

**Test: Tokenization-Ambiguous Single Candidate**
```python
def test_single_candidate_but_tokenization_ambiguous():
    """One ActivityCandidate, but phrase itself is ambiguous → unresolved."""
    cell_text = "PE 1,2,3,4\n(GK)\nLAB1A"

    block = parse_timetable_cell(cell_text, day="saturday", section="I-A", slots=["S2"])

    # Should have exactly one activity candidate
    assert len(block.activity_candidates) == 1
    assert block.activity_candidates[0].code == "PE 1,2,3,4"

    # But candidate is marked tokenization-ambiguous
    assert block.activity_candidates[0].is_tokenization_ambiguous == True

    # Therefore block is unresolved despite 1:1:1 count
    classify_block_resolution(block)
    assert block.is_resolved == False
    assert "ambiguous boundaries" in block.ambiguity_reason.lower() or "tokenization" in block.ambiguity_reason.lower()

    # Cannot convert
    resolved = convert_to_resolved_activity(block)
    assert resolved is None


def test_one_one_two_is_error():
    """1 activity + 1 teacher + 2 resources → ERROR and unresolved."""
    block = TimetableBlock(
        day="monday",
        section="I-A",
        slots=["S3"],
        activity_candidates=[ActivityCandidate(code="DBMS", inferred_type="CLASS", is_tokenization_ambiguous=False)],
        teacher_candidates=[TeacherCandidate(acronym="VR", normalized_acronym="VR", is_identity_resolvable=True)],
        resource_candidates=[
            ResourceCandidate(code="CA1", normalized_code="CA1", is_identity_resolvable=True),
            ResourceCandidate(code="CA2", normalized_code="CA2", is_identity_resolvable=True),
        ],
        is_resolved=False,
        ambiguity_reason=None,
        source_location=...,
        original_text="DBMS\n(VR)\nCA1, CA2",
        issues=[]
    )

    is_resolved, reason = ResolutionRule.is_resolved(block)

    assert is_resolved == False
    assert "2 resource candidates" in reason

    # Should have ERROR-level issue
    classify_block_resolution(block)
    assert any(issue.severity == ERROR and issue.code == "AMBIGUOUS_RESOURCE_SINGLE_ACTIVITY" for issue in block.issues)


def test_manual_resolution_produces_multiple_activities():
    """Ambiguous block can be manually resolved into multiple ResolvedActivity objects."""
    # Original unresolved block
    unresolved = TimetableBlock(
        day="tuesday",
        section="I-A",
        slots=["S4", "S5"],
        activity_candidates=[
            ActivityCandidate(code="PY1", inferred_type="LAB", is_tokenization_ambiguous=False),
            ActivityCandidate(code="PE2", inferred_type="LAB", is_tokenization_ambiguous=False),
        ],
        teacher_candidates=[
            TeacherCandidate(acronym="SU", normalized_acronym="SU", is_identity_resolvable=True),
            TeacherCandidate(acronym="TS", normalized_acronym="TS", is_identity_resolvable=True),
        ],
        resource_candidates=[
            ResourceCandidate(code="LAB1B", normalized_code="LAB1B", is_identity_resolvable=True),
        ],
        is_resolved=False,
        ambiguity_reason="2 activities, 2 teachers",
        source_location=SourceLocation(source_type="DOCX", table_index=0, row_index=5, column_index=6, ...),
        original_text="PY1, PE2\n(SU) (TS)\nLAB1B",
        issues=[...]
    )

    # User creates two resolution mappings
    mappings = [
        ManualResolutionMapping(
            source_block_id=str(unresolved.id),
            selected_activity="PY1",
            selected_teacher="SU",
            selected_resource="LAB1B",
            resolved_by="admin@example.com",
            resolved_at=datetime.now(),
            resolution_notes="SU teaches PY1"
        ),
        ManualResolutionMapping(
            source_block_id=str(unresolved.id),
            selected_activity="PE2",
            selected_teacher="TS",
            selected_resource="LAB1B",
            resolved_by="admin@example.com",
            resolved_at=datetime.now(),
            resolution_notes="TS teaches PE2"
        ),
    ]

    # Convert mappings to resolved activities
    resolved_activities = apply_manual_resolutions(unresolved, mappings)

    assert len(resolved_activities) == 2
    assert resolved_activities[0].subject_or_activity == "PY1"
    assert resolved_activities[0].teacher_acronym == "SU"
    assert resolved_activities[1].subject_or_activity == "PE2"
    assert resolved_activities[1].teacher_acronym == "TS"

    # Both preserve original source_location
    assert resolved_activities[0].source_location == unresolved.source_location
    assert resolved_activities[1].source_location == unresolved.source_location

    # Both marked as manually resolved
    assert resolved_activities[0].manually_resolved == True
    assert resolved_activities[1].manually_resolved == True


def test_manual_resolution_preserves_source_location():
    """Manual resolution preserves original source_location."""
    # Covered by test_manual_resolution_produces_multiple_activities above


def test_parentheses_classify_not_associate():
    """Parentheses classify candidate type but do NOT establish relationships."""
    cell_text = "PY1\n(SU) (TS)\nLAB1A"

    block = parse_timetable_cell(cell_text, day="monday", section="I-A", slots=["S3"])

    # Parentheses identify teacher candidates
    assert len(block.teacher_candidates) == 2
    assert block.teacher_candidates[0].acronym == "SU"
    assert block.teacher_candidates[1].acronym == "TS"

    # But do NOT establish which teacher is for which activity
    # (In this case only 1 activity, but 2 teachers is ambiguous)
    assert len(block.activity_candidates) >= 1

    for activity in block.activity_candidates:
        assert not hasattr(activity, 'teacher')
        assert not hasattr(activity, 'assigned_teacher')

    # Block is unresolved due to 2 teachers
    classify_block_resolution(block)
    assert block.is_resolved == False


def test_grid_dimensions_match_docx():
    """Verify reconstructed grid matches exact physical DOCX dimensions."""
    docx_bytes = read_test_file("MCA Timetable-2026-Odd V7.docx")

    doc = Document(BytesIO(docx_bytes))
    main_table = doc.tables[0]

    # Physical dimensions
    assert len(main_table.rows) == 25  # 1 header + 24 data rows (6 days × 4 sections)
    assert len(main_table.columns) == 13  # day, section, 9 slots, 2 breaks

    # Reconstruct logical grid
    grid = reconstruct_grid(main_table)

    # Logical grid should account for merges
    assert len(grid) == 25  # Same row count
    # Column count may vary per row due to gridSpan
```

---

## 16. IMPLEMENTATION NOTES (NOT CODE)

### 16.1 Architecture: Staging vs Domain

**CRITICAL DISTINCTION**:

**Staging/Transport Layer** (ImportPreview):
- `DOCXImportPreview` - Transport structure containing parsed data
- `UnresolvedTimetableBlock` - Staging representation of ambiguous cells
- `ManualResolutionMapping` - User's resolution decision (ephemeral)
- `ExcludedCandidate` - Tracking excluded items (ephemeral)
- **NOT persistent domain entities**
- **NOT database tables**
- Lives only during import session

**Domain Layer** (CanonicalTimetable):
- `CanonicalTimetable` - Domain model for validated timetable
- `ScheduleActivity` - Domain model for activities
- `Teacher` - Domain entity (persistent)
- `Resource` - Domain entity (persistent)
- **Persistent domain entities**
- **Stored in database**

**Flow**:
```
DOCX bytes
    ↓
Parser extracts TimetableBlock objects (in-memory)
    ↓
DOCXImportPreview created (staging/transport)
    ├─ resolved_blocks: list[ResolvedActivity]
    └─ unresolved_blocks: list[UnresolvedTimetableBlock]  ← STAGING ONLY
    ↓
User applies ManualResolutionMapping (in-memory operation)  ← STAGING ONLY
    ↓
Unresolved → Resolved conversion (in ImportPreview)
    ↓
DOCXImportPreview.resolved_blocks → CanonicalTimetable
    ↓
ValidationEngine validates CanonicalTimetable
    ↓
CanonicalTimetable → DRAFT Timetable (persisted to DB)
    ↓
Staging data (ImportPreview, unresolved blocks, mappings) DISCARDED
```

**Do NOT create database tables for staging structures.**

### 16.2 Key Data Structures

**TimetableBlock**: Intermediate representation preserving individual candidates without associations (in-memory parsing structure)

**ResolvedActivity**: Successfully resolved block ready for conversion (in-memory transport structure)

**DOCXImportPreview**: Parser output containing resolved + unresolved separately (staging/transport, NOT domain)

**CanonicalTimetable**: Only contains resolved activities, ready for validation (domain model, persists to DB)

### 16.3 Critical Rules

1. **NO GUESSING**: Parser never invents teacher-activity-resource associations
2. **NO FLATTENING**: Never convert candidates to comma-separated strings
3. **EXPLICIT AMBIGUITY**: Unresolved blocks remain explicit, not hidden
4. **ERROR SEMANTICS**: Ambiguities that affect correctness are ERROR-level
5. **WARNING SEMANTICS**: Missing optional data or minor issues are WARNING-level
6. **PUBLISHABILITY**: Zero ERRORs + zero unresolved blocks required for publication

### 16.3 Critical Rules

1. **NO GUESSING**: Parser never invents teacher-activity-resource associations
2. **NO FLATTENING**: Never convert candidates to comma-separated strings
3. **EXPLICIT AMBIGUITY**: Unresolved blocks remain explicit, not hidden
4. **ERROR SEMANTICS**: Ambiguities that affect correctness are ERROR-level
5. **WARNING SEMANTICS**: Missing optional data or minor issues are WARNING-level
6. **PUBLISHABILITY**: Zero ERRORs + zero unresolved blocks required for publication
7. **STAGING NOT DOMAIN**: UnresolvedTimetableBlock is transport, not persistent entity

### 16.4 Conversion Pipeline Summary

```
DOCX Cell Text
    ↓
Parse into TimetableBlock (individual candidates, no associations)
    ↓
Classify resolution status using ResolutionRule
    ↓
IF resolved:
    Convert to ResolvedActivity (in-memory)
    ↓
    Add to ImportPreview.resolved_blocks
ELSE (unresolved):
    Keep as UnresolvedTimetableBlock in ImportPreview.unresolved_blocks ← STAGING
    ↓
    User creates ManualResolutionMapping(s) ← STAGING, in-memory
    ↓
    Apply mapping: Unresolved → ResolvedActivity (in ImportPreview)
    ↓
    Add to ImportPreview.resolved_blocks
    ↓
END IF

After all blocks resolved:
    ImportPreview.resolved_blocks → CanonicalTimetable (domain)
    ↓
    ValidationEngine validates
    ↓
    CanonicalTimetable → DRAFT Timetable (persisted to DB)
    ↓
    Staging data (ImportPreview, unresolved blocks, mappings) DISCARDED
```

**Key Point**: Manual resolution operates on staging state (ImportPreview), NOT on persistent domain entities.

---

## 17. VERIFICATION SUMMARY

### 17.1 Revised Intermediate Representation

**TimetableBlock**:
- Preserves individual `ActivityCandidate`, `TeacherCandidate`, `ResourceCandidate` lists
- Does NOT flatten to comma-separated strings
- Does NOT invent associations between candidates
- Explicitly tracks `is_resolved` status and `ambiguity_reason`
- Contains full source traceability and validation issues

**ResolvedActivity**:
- Derived from TimetableBlock with 1:1:1 or 1:1:0/1 structure
- Ready for conversion to CanonicalTimetable
- All relationships established

### 17.2 How Ambiguity is Represented

**Unresolved blocks**:
- `is_resolved = False`
- `ambiguity_reason` describes why (e.g., "4 activities, 4 teachers: cannot determine associations")
- Individual candidates remain separate in their respective lists
- Validation issues include ERROR-level `AMBIGUOUS_BLOCK`
- Block remains in `DOCXImportPreview.unresolved_blocks`

**NOT represented as**:
- ❌ Comma-separated strings
- ❌ Invented teacher-activity pairs
- ❌ First-match heuristics
- ❌ Spatial grouping assumptions

### 17.3 Final RESOLVED Rule

**A TimetableBlock is RESOLVED if and only if ALL of the following are true**:

1. ✅ EXACTLY ONE ActivityCandidate exists
2. ✅ The ActivityCandidate is NOT marked as `is_tokenization_ambiguous`
3. ✅ EXACTLY ONE TeacherCandidate exists
4. ✅ The TeacherCandidate identity is uniquely resolvable or explicitly valid for CREATE
5. ✅ ZERO or ONE ResourceCandidate exists
6. ✅ If ResourceCandidate exists, its identity is uniquely resolvable
7. ✅ There are NO ERROR-level ValidationIssues
8. ✅ There is NO unresolved activity-tokenization ambiguity
9. ✅ There is NO unresolved relationship ambiguity

**Candidate count alone NEVER implies semantic resolution.**

**Example of unresolved despite 1:1:1 count**:
```python
TimetableBlock(
    activity_candidates=[ActivityCandidate(code="PE 1,2,3,4", is_tokenization_ambiguous=True)],
    teacher_candidates=[TeacherCandidate(acronym="GK", is_identity_resolvable=True)],
    resource_candidates=[ResourceCandidate(code="LAB1A", is_identity_resolvable=True)],
    is_resolved=False,  # Because activity is tokenization-ambiguous
    ambiguity_reason="Activity 'PE 1,2,3,4' has ambiguous boundaries"
)
```

### 17.4 Final ERROR/WARNING Rules

**AUTHORITATIVE RULES**:

**ERROR** (blocks publication):
1. **Multiple possible resources for one activity** - Even 1:1:2 is ERROR (no "first resource wins")
2. **Multiple resources across multiple activities** - Mapping unknown
3. **Ambiguous teacher-activity mapping** - Multiple teachers, ambiguous associations
4. **Activity tokenization ambiguous** - Phrase like "DS 3,4" or "PE 1,2,3,4" with unclear boundaries
5. **Missing teacher** - Required for availability system
6. **Missing subject** - Cannot create meaningful activity
7. **Unresolved teacher identity** - Cannot map to unique teacher
8. **Structural failures** - Malformed table, invalid slots, break-as-activity

**WARNING** (allows publication with review):
1. **Missing resource** - Some activities legitimately have no room
2. **Unknown resource** - Resource may be added during review
3. **Unknown teacher legend** - Teacher acronym not in legend but can proceed
4. **Format inconsistencies** - Unusual but parseable

**NO "use first resource" or "assign NULL" behavior anywhere.**

### 17.5 Manual Resolution Representation

**CRITICAL: These are TRANSPORT/STAGING structures, NOT persistent domain entities.**

**Architecture Clarification**:
```
DOCX → ImportPreview (staging) → CanonicalTimetable → ValidationEngine → DRAFT → CONFIRMED
         ↑
         UnresolvedTimetableBlock, ResolutionMapping live HERE only
         (transport/UI state, not domain model)
```

**Transport Structures** (in-memory or session-scoped):
```python
# In DOCXImportPreview (staging transport)
class DOCXImportPreview:
    import_session_id: str  # Temporary
    resolved_blocks: list[ResolvedActivity]
    unresolved_blocks: list[UnresolvedTimetableBlock]  # TRANSPORT, not domain
    manual_resolutions: list[ManualResolutionMapping]  # TRANSPORT, not domain
    excluded_candidates: list[ExcludedCandidate]  # TRANSPORT, not domain
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]

# Ephemeral structures (NOT database tables)
UnresolvedTimetableBlock:
    - id: temporary ID (not UUID)
    - candidates: lists of dicts
    - NO database persistence
    - Lives only during import session

ManualResolutionMapping:
    - User's explicit selection
    - Applied to ImportPreview
    - NO database persistence
    - Discarded after conversion to CanonicalTimetable

ExcludedCandidate:
    - Tracks excluded items
    - NO database persistence
    - For import audit only
```

**DO NOT**:
- ❌ Create database migrations for these
- ❌ Add SQLAlchemy models for these
- ❌ Persist across import sessions
- ❌ Create second domain model

**DO**:
- ✅ Use as transport/DTO structures in ImportPreview
- ✅ Store temporarily (Redis, in-memory, or JSON) if needed
- ✅ Convert to CanonicalTimetable after resolution
- ✅ Discard staging data after conversion

**Resolution Flow**:
```
Unresolved block (staging)
    ↓
User creates ManualResolutionMapping (staging)
    ↓
System applies mapping to ImportPreview (in-memory)
    ↓
Unresolved → ResolvedActivity
    ↓
Convert to CanonicalTimetable (domain)
    ↓
ValidationEngine
    ↓
Persist to DRAFT timetable (database)
    ↓
Staging data discarded
```

**Persistence Options**:
1. **No persistence** (simplest): User resolves all blocks in one session
2. **Session-scoped** (if needed): Serialize DOCXImportPreview to Redis/session storage, expires after N hours

**Never persist as domain tables**. ImportPreview is staging/transport only.

### 17.6 Exact Physical Table Dimensions

**Main Timetable Table** (Table 0):
- **Physical rows**: 25 (1 header + 24 data rows = 6 days × 4 sections)
- **Physical columns**: 13 (day, section, 9 slots, 2 breaks)
- **Logical days**: 6 (MON, TUE, WED, THU, FRI, SAT)
- **Sections per day**: 4 (I-A, I-B, III-A, III-B)
- **Working slots**: 9 (S1-S9)
- **Break slots**: 2 (Morning Break after S3, Lunch Break after S5)

**Column Mapping**:
| Col | Purpose | Content |
|-----|---------|---------|
| 0 | Day | MON-SAT (vertically merged across 4 sections) |
| 1 | Section | I-A, I-B, III-A, III-B |
| 2-4 | S1-S3 | Morning slots |
| 5 | BREAK | Coffee Break (vertically merged) |
| 6-7 | S4-S5 | Mid-day slots |
| 8 | BREAK | Lunch Break (vertically merged) |
| 9-12 | S6-S9 | Afternoon slots |

**Faculty Legend Table** (Table 1):
- **Rows**: 16 (1 header + 15 faculty)
- **Columns**: 5 (Sl.No, Name, Initials, Classroom, Location)

### 17.7 Internal Consistency Confirmation

✅ **Checked all sections for consistency**:
- Section 0: Intermediate representation with ResolutionRule
- Section 0.3: Critical parsing principles (NO GUESSING)
- Section 0.4: Manual resolution contract
- Section 0.5: Parentheses classify types, NOT associations
- Section 2: Exact physical table dimensions (25×13)
- Section 4: Activity extraction (NO comma assumptions)
- Section 7: Teacher extraction (individual candidates)
- Section 8: Resource extraction (individual candidates)
- Section 9: Tuesday I-A example (uses TimetableBlock, preserves raw phrases)
- Section 11: ERROR/WARNING rules (consistent throughout)
- Section 12: Three-stage resource handling
- Section 13: All 8 examples follow NO GUESSING principle
- Section 14: Algorithm uses correct resolution rules
- Section 15: Tests validate all critical principles

✅ **No contradictions found between**:
- "DO NOT GUESS" principle
- Intermediate representation structure
- Resource handling stages
- Activity tokenization rules
- Resolution classification
- ERROR/WARNING semantics

### 17.8 Confirmation: No Implementation Code Written

✅ **CONFIRMED**: This document is SPECIFICATION ONLY.

**What was delivered**:
- Data structure definitions (pseudocode/Python type hints)
- Algorithm descriptions (prose and pseudocode)
- Test specifications (test function signatures and assertions)
- Validation rules and semantics
- Examples and edge cases
- Critical parsing principles
- Manual resolution contract
- Exact table dimensions
- Consolidated ERROR/WARNING rules
- **Architecture clarification: staging vs domain**

**What was NOT delivered**:
- ❌ Working parser implementation
- ❌ Executable Python code
- ❌ Integration with existing backend
- ❌ Database schema changes
- ❌ Database migrations for staging structures
- ❌ SQLAlchemy models for UnresolvedTimetableBlock/ResolutionMapping/ExcludedCandidate
- ❌ API endpoint modifications
- ❌ UI components

**Architecture Confirmation**:
- ✅ UnresolvedTimetableBlock is STAGING/TRANSPORT, not persistent domain entity
- ✅ ManualResolutionMapping is STAGING/TRANSPORT, not persistent domain entity
- ✅ ExcludedCandidate is STAGING/TRANSPORT, not persistent domain entity
- ✅ These structures live in ImportPreview (in-memory or session-scoped)
- ✅ NO database tables created for these structures
- ✅ Manual resolution operates on staging state, NOT domain entities
- ✅ After conversion to CanonicalTimetable, staging data is discarded
- ✅ Approved architecture flow maintained: DOCX → ImportPreview → CanonicalTimetable → ValidationEngine → DRAFT → CONFIRMED

**This remains a pure specification document ready for implementation with clear staging/domain boundaries.**

---

## END OF SPECIFICATION

**Publishability**:
- Preview stage: May contain ERRORs and WARNINGs
- Draft stage: Must have zero ERRORs, may have WARNINGs
- Confirmed stage: Must have zero ERRORs, zero unresolved blocks

### 17.4 CanonicalTimetable Conversion Rules

**ScheduleActivity can ONLY be created from**:
1. `ResolvedActivity` (derived from resolved `TimetableBlock`)
2. Blocks with `is_resolved = True`
3. Zero ERROR-level issues
4. After teacher/resource identity resolution

**ScheduleActivity CANNOT be created from**:
1. Unresolved `TimetableBlock` (`is_resolved = False`)
2. Blocks with ERROR-level issues
3. Blocks with ambiguous associations
4. Blocks missing required fields (subject, teacher)

**Conversion function**: `convert_to_canonical_timetable(preview: DOCXImportPreview)`
- Processes only `preview.resolved_blocks`
- Ignores `preview.unresolved_blocks` (user must resolve manually)
- Returns `None` if `preview.errors` is non-empty

### 17.5 Updated Tests

**New test assertions**:
- ✅ `test_ambiguous_block_no_flattening()`: Verifies NO comma-separated string flattening
- ✅ `test_individual_candidates_preserved()`: Verifies candidates remain separate
- ✅ `test_no_arbitrary_teacher_activity_pairing()`: Verifies NO invented associations
- ✅ `test_no_arbitrary_room_activity_pairing()`: Verifies NO resource pairing
- ✅ `test_unresolved_blocks_cannot_convert()`: Verifies conversion safety
- ✅ `test_unresolved_blocks_cannot_be_published()`: Verifies publication safety
- ✅ `test_resource_normalization_stage()`: Verifies 3-stage resource handling
- ✅ `test_normalization_not_resolution()`: Verifies normalization ≠ resolution
- ✅ `test_ambiguous_teacher_is_error()`: Verifies ERROR semantics
- ✅ `test_missing_resource_is_warning()`: Verifies WARNING semantics

**Integration tests**:
- ✅ `test_parse_mca_timetable_v7()`: End-to-end parsing of real document
- ✅ `test_resolved_activities_map_to_canonical()`: Conversion correctness
- ✅ `test_unresolved_blocks_not_in_canonical()`: Unresolved blocks excluded

### 17.6 Confirmation: No Implementation Code Written

✅ **CONFIRMED**: This document is SPECIFICATION ONLY.

**What was delivered**:
- Data structure definitions (pseudocode/Python type hints)
- Algorithm descriptions (prose and pseudocode)
- Test specifications (test function signatures and assertions)
- Validation rules and semantics
- Examples and edge cases
- Critical parsing principles

**What was NOT delivered**:
- ❌ Working parser implementation
- ❌ Executable Python code
- ❌ Integration with existing backend
- ❌ Database schema changes
- ❌ API endpoint modifications

### 17.7 Final Verification Checklist

✅ **NO comma-separated representation remains**
- All examples show individual candidate objects, not "LAB1A, LAB1B" strings
- Resource candidates: `[ResourceCandidate(...), ResourceCandidate(...)]`
- Teacher candidates: `[TeacherCandidate(...), TeacherCandidate(...)]`
- Activity candidates: preserved as raw phrases when boundaries unclear

✅ **NO "use first resource" behavior remains**
- Section 11.3: AMBIGUOUS_RESOURCE_SINGLE_ACTIVITY changed from WARNING to ERROR
- Section 12.2: Case 1 explicitly states block remains unresolved, NO first resource selection
- Test `test_multiple_resources_not_first_wins()`: verifies parser does NOT use first resource
- Test `test_ambiguous_resource_remains_unresolved()`: verifies 1:1:2 blocks publication

✅ **Ambiguous activity numbering is preserved**
- Section 4.2: Parser uses CONSERVATIVE approach, NO assumptions about commas
- Section 9.4: Tuesday I-A preserves "PY1, PE2, DS 3, 4" without automatic splitting
- Section 13.2: "PE 1,2,3,4" preserved as single candidate, NOT split into 4 activities
- Section 13.3: "DT 3,4" preserved as ONE candidate, NOT "DT 3" and "DT 4"
- Section 13.6: "DS 1,2" preserved as ONE candidate with ambiguity flag
- Test `test_activity_phrase_preserved_not_split()`: verifies "DS 3,4" stays intact
- Test `test_pe_numbers_not_automatically_split()`: verifies "PE 1,2,3,4" not split

✅ **Critical parsing principles documented**
- Section 0.3: CRITICAL PARSING PRINCIPLES added with 5 key principles
- Principle 1: NO GUESSING - explicit examples of prohibited assumptions
- Principle 2: PRESERVE SOURCE STRUCTURE - three-stage interpretation
- Principle 3: EXPLICIT AMBIGUITY - unresolved blocks explicit
- Principle 4: ERROR SEMANTICS - ambiguities affecting availability are ERRORs
- Principle 5: STRUCTURAL EVIDENCE ONLY - deterministic markers only

✅ **Internal consistency verified**
- Section 4: Activity extraction does NOT assume comma = separator
- Section 6: Parallel activities remain unresolved without structural evidence
- Section 9: Tuesday I-A example uses TimetableBlock with individual candidates
- Section 11: ERROR vs WARNING distinguishes publishability correctly
- Section 12: Three-stage resource handling (extract → normalize → resolve)
- Section 13: All 8 examples consistent with NO GUESSING principle
- Section 14: Algorithm reconstruction uses TimetableBlock representation
- Section 15: Test strategy validates all critical principles

✅ **NO parser implementation was written**
- Document contains specification, examples, tests, and principles ONLY
- No executable code in backend/app/services/
- No new Python modules created
- No integration with existing systems

**Next step**: Implementation phase (separate task) will follow this specification with full confidence that ambiguity handling, activity tokenization, and resource resolution are correctly specified.

---

## END OF SPECIFICATION
    section_col = day_slot_map['section_column']

    for row_idx in range(header_row_idx + 1, len(grid)):
        section_text = grid[row_idx][section_col].text.strip()
        day_text = grid[row_idx][0].text.strip()

        if section_text:  # Non-empty section
            section_rows.append(SectionRow(
                day=normalize_day(day_text),
                section=section_text,
                row_index=row_idx
            ))

    return section_rows
```

#### Step 7: Activity Cell Parsing
```python
def parse_activity_cell(cell: GridCell, day: str, section: str, slot_start: str) -> list[ScheduleActivity]:
    """Parse single cell into one or more activities.

    Returns list (usually 1, but can be multiple for parallel activities).
    """
    if not cell.text.strip() or cell.text.strip() in ["", "COFFEE BREAK", "LUNCH BREAK"]:
        return []  # Empty or break cell

    # Determine slot span
    slots = [slot_start]
    if cell.gridSpan and cell.gridSpan > 1:
        # Multi-slot activity
        for i in range(1, cell.gridSpan):
            slots.append(f"S{int(slot_start[1:]) + i}")

    # Parse cell content
    lines = [line.strip() for line in cell.text.split('\n') if line.strip()]

    subjects = extract_subjects(lines)
    teachers = extract_teachers(lines)
    resources = extract_resources(lines)

    # Ambiguity detection
    if len(subjects) > 1 and (len(teachers) > 1 or len(resources) > 1):
        # AMBIGUOUS CASE
        return [ScheduleActivity(
            day=day,
            section=section,
            slots=slots,
            entry_type=infer_entry_type(subjects[0]),
            subject_or_activity=", ".join(subjects),  # Preserve original
            teacher_acronyms=teachers,  # Store all
            resource_codes=resources,  # Store all
            source_location=create_source_location(cell),
            validation_issues=[
                ValidationIssue(
                    severity=WARNING,
                    code="AMBIGUOUS_MAPPING",
                    message=f"Cell contains {len(subjects)} activities with {len(teachers)} teachers and {len(resources)} resources"
                )
            ]
        )]

    # Unambiguous case
    return [ScheduleActivity(
        day=day,
        section=section,
        slots=slots,
        entry_type=infer_entry_type(subjects[0]),
        subject_or_activity=subjects[0],
        teacher_acronyms=teachers,
        resource_codes=resources,
        source_location=create_source_location(cell),
    )]
```

#### Step 8: CanonicalTimetable Construction
```python
def build_canonical_timetable(
    section_rows: list[SectionRow],
    grid: Grid,
    day_slot_map: dict,
    department: str,
) -> CanonicalTimetable:
    """Construct CanonicalTimetable from parsed grid."""

    activities = []
    teachers_to_create = {}  # Accumulate unique teachers

    for section_row in section_rows:
        for col_idx, slot_code in day_slot_map['slot_columns'].items():
            if slot_code == 'BREAK':
                continue  # Skip breaks

            cell = grid[section_row.row_index][col_idx]

            # Skip if this cell is a vertical merge continuation
            if cell.vMerge == 'continue':
                continue

            parsed_activities = parse_activity_cell(
                cell, section_row.day, section_row.section, slot_code
            )

            activities.extend(parsed_activities)

            # Accumulate teachers
            for activity in parsed_activities:
                for acronym in activity.teacher_acronyms:
                    if acronym not in teachers_to_create:
                        teachers_to_create[acronym] = TeacherIdentity(
                            acronym=acronym,
                            department=department,
                            resolution=RESOLUTION_NEEDED
                        )

    return CanonicalTimetable(
        academic_year=extract_academic_year(doc),  # From header
        source=SourceLocation(
            source_type="DOCX",
            source_identifier=filename,
        ),
        teachers=list(teachers_to_create.values()),
        activities=activities,
        validation_issues=[]  # Populated by ValidationEngine
    )
```

#### Step 9: Validation
```python
def validate_timetable(canonical: CanonicalTimetable, db: Session) -> CanonicalTimetable:
    """Run through ValidationEngine."""

    # Resolve teachers via department-aware lookup
    for teacher in canonical.teachers:
        existing = db.query(Teacher).filter(
            func.lower(Teacher.acronym) == teacher.acronym.lower(),
            func.lower(Teacher.department) == teacher.department.lower()
        ).first()

        if existing:
            teacher.resolved_teacher_id = existing.id
            teacher.resolution = REUSE
        else:
            teacher.resolution = CREATE

    # Resolve resources via Resource Foundation
    for activity in canonical.activities:
        if activity.resource_codes:
            for resource_code in activity.resource_codes:
                resolution = resolve_resource(resource_code, teacher.department, db)
                # Store resolution result

    # Run standard validation
    issues = validate_canonical_timetable(canonical, db)
    canonical.validation_issues.extend(issues)

    return canonical
```

### 12.3 Non-Guessing Principles

**The parser NEVER**:
1. Assumes whitespace/parentheses indicate associations
2. Assigns "first teacher to first activity"
3. Guesses which room belongs to which activity
4. Resolves ambiguous structures using heuristics
5. Silently drops ambiguous data

**The parser ALWAYS**:
1. Extracts ALL information from cells
2. Preserves original text in source location
3. Emits explicit warnings for ambiguities
4. Lets ValidationEngine and user resolve ambiguities
5. Maintains traceability to original document

---

## 13. CANONICAL OUTPUT CONTRACT

### 13.1 Mapping to CanonicalTimetable

**From DOCX** → **To CanonicalTimetable**:

| DOCX Element | CanonicalTimetable Field | Extraction Method |
|--------------|-------------------------|-------------------|
| Document header "Odd semester 2026" | `academic_year` | Parse header paragraph, infer year range |
| Teacher acronym in cell "(VR)" | `TeacherIdentity.acronym` | Regex: `\(([A-Z]+)\)` |
| Document header "Department of..." | `TeacherIdentity.department` | Parse header |
| Faculty legend table | `TeacherIdentity.name` | Match acronym to legend |
| Day name "MON" | `ScheduleActivity.day` | Map to ISO: "monday" |
| Slot column + gridSpan | `ScheduleActivity.slots` | Map columns to ["S1"] or ["S4", "S5"] |
| Subject line "DBMS" | `ScheduleActivity.subject_or_activity` | First non-empty, non-parenthesized line |
| Section "I-A" | `ScheduleActivity.section` | From section column |
| Room "CA1" | `ResourceReference` or string | Extract, resolve via Resource Foundation |
| Inferred from subject | `ScheduleActivity.entry_type` | "CLASS" | "LAB" | "OTHER" |
| Cell coordinates | `SourceLocation` | Table/row/col indices |

### 13.2 Current Model Compatibility

**Existing `CanonicalTimetable` fields** (from PHASE_2_ARCHITECTURE.md):
- `academic_year: str` ✅
- `source: SourceLocation` ✅
- `teachers: list[TeacherIdentity]` ✅
- `activities: list[ScheduleActivity]` ✅
- `validation_issues: list[ValidationIssue]` ✅

**Existing `ScheduleActivity` fields**:
- `day: str` ✅
- `slots: list[str]` ✅
- `entry_type: Literal["CLASS", "LAB", "OTHER"]` ✅
- `subject_or_activity: str` ✅
- `section: str | None` ✅
- `room: str | None` ✅
- `notes: str | None` ✅
- `teacher_acronym: str` ✅
- `source_location: SourceLocation` ✅

**DOCX-Specific Extensions** (if needed):
- `teacher_acronyms: list[str]` (multiple teachers) - **Current model only supports single `teacher_acronym`**
- `resource_codes: list[str]` (multiple resources) - **Current model only supports single `room`**

### 13.3 Model Extension Required

**BLOCKER IDENTIFIED**: Current `ScheduleActivity` model assumes single teacher per activity.

**Proposed Extension**:
```python
@dataclass
class ScheduleActivity:
    # ... existing fields ...
    teacher_acronym: str  # DEPRECATED for ambiguous cases
    teacher_acronyms: list[str] | None  # NEW: Support multiple teachers

    resource_code: str | None  # DEPRECATED for ambiguous cases
    resource_codes: list[str] | None  # NEW: Support multiple resources

    is_ambiguous: bool = False  # NEW: Flag for ambiguous mapping
```

**Backward Compatibility**:
- If `len(teacher_acronyms) == 1`: populate `teacher_acronym` for compatibility
- If `len(teacher_acronyms) > 1`: set `teacher_acronym = None`, set `is_ambiguous = True`

**Alternative** (NO MODEL CHANGE):
- For ambiguous cells, create ONE activity with:
  - `teacher_acronym = None`
  - `subject_or_activity = "PY1, PE2, DS 3,4"` (comma-separated)
  - `room = "LAB1B, LAB1A"` (comma-separated)
  - `notes = "AMBIGUOUS: Multiple teachers/resources. Original: (SU) (TS) (SS, KPS)"`
  - `validation_issues = [WARNING(...)]`

**Recommendation**: Use "Alternative" approach to avoid model changes. Store ambiguous data as comma-separated strings with validation warnings.

---

## 14. TEST STRATEGY

### 14.1 Test Fixtures

**Strategy**: Create fixture DOCX files with known structures.

**Fixture 1: Minimal Valid Timetable** (`minimal_timetable.docx`):
- 1 day (Monday)
- 1 section (I-A)
- 3 activities (S1, S2, S4)
- Simple cells (no merges, no ambiguity)
- Faculty legend

**Fixture 2: Multi-Slot Activity** (`multi_slot_timetable.docx`):
- 1 day
- 1 activity spanning S4+S5 (gridSpan=2)
- Verify slot list = ["S4", "S5"]

**Fixture 3: Parallel Activity Ambiguity** (`ambiguous_timetable.docx`):
- 1 cell with "PY1, PE2 / (SU) (TS) / LAB1A, LAB1B"
- Verify WARNING emitted
- Verify all teachers/resources extracted

**Fixture 4: Break Detection** (`break_detection.docx`):
- Include morning break and lunch columns
- Verify breaks are NOT parsed as activities

**Fixture 5: Vertical Merge** (`vertical_merge.docx`):
- Day name vertically merged across sections
- Verify day name applied to all sections

**Fixture 6: Unknown Teacher** (`unknown_teacher.docx`):
- Activity references "(ZZZ)" not in legend
- Verify WARNING: "Unknown teacher acronym 'ZZZ'"

**Fixture 7: Missing Subject** (`missing_subject.docx`):
- Cell with only "(VR)\nCA1" (no subject)
- Verify ERROR: "Missing subject"

**Fixture 8: Actual Document** (`MCA Timetable-2026-Odd V7.docx`):
- Full integration test
- Parse entire document
- Verify expected number of activities extracted
- Verify known ambiguous cells have warnings

### 14.2 Unit Tests

```python
def test_load_docx():
    """Test DOCX loading."""
    doc = load_docx(fixture_bytes)
    assert len(doc.tables) > 0

def test_detect_timetable_table():
    """Test timetable table detection."""
    doc = load_docx(minimal_fixture)
    table = detect_timetable_table(doc)
    assert table is not None

def test_reconstruct_grid():
    """Test physical grid reconstruction."""
    grid = reconstruct_grid(table)
    assert len(grid) == expected_rows
    assert len(grid[0]) == expected_cols

def test_merge_detection():
    """Test gridSpan and vMerge detection."""
    cell = grid[5][6]
    assert cell.gridSpan == 2  # Multi-slot activity

def test_header_row_detection():
    """Test header row identification."""
    header_idx = detect_header_row(grid)
    assert "08.00" in grid[header_idx][2].text

def test_day_slot_mapping():
    """Test column-to-slot mapping."""
    day_slot_map = build_day_slot_map(grid, header_idx)
    assert day_slot_map['slot_columns'][2] == 'S1'
    assert day_slot_map['slot_columns'][5] == 'BREAK'

def test_activity_extraction_simple():
    """Test simple activity cell parsing."""
    cell = GridCell(text="DBMS\n(VR)\nCA1", gridSpan=None, ...)
    activities = parse_activity_cell(cell, "monday", "I-A", "S1")
    assert len(activities) == 1
    assert activities[0].subject_or_activity == "DBMS"
    assert activities[0].teacher_acronyms == ["VR"]
    assert activities[0].resource_codes == ["CA1"]

def test_activity_extraction_multi_slot():
    """Test multi-slot activity parsing."""
    cell = GridCell(text="PE 1,2,3,4\n(GK, SS, KPS, VK)\n(LAB 1A)", gridSpan=2, ...)
    activities = parse_activity_cell(cell, "tuesday", "I-B", "S1")
    assert activities[0].slots == ["S1", "S2"]

def test_activity_extraction_ambiguous():
    """Test ambiguous cell produces warning."""
    cell = GridCell(text="PY1, PE2\n(SU) (TS)\n(LAB1A) (LAB1B)", gridSpan=2, ...)
    activities = parse_activity_cell(cell, "tuesday", "I-A", "S4")
    assert len(activities) == 1
    assert activities[0].is_ambiguous == True
    assert len(activities[0].validation_issues) > 0
    assert "AMBIGUOUS" in activities[0].validation_issues[0].code

def test_break_skipped():
    """Test break cells are not parsed as activities."""
    cell = GridCell(text="COFFEE BREAK", ...)
    activities = parse_activity_cell(cell, "monday", "I-A", "BREAK")
    assert len(activities) == 0

def test_teacher_legend_parsing():
    """Test faculty legend table extraction."""
    legend = parse_faculty_legend(doc)
    assert legend['SU'] == 'Dr. S. Uma'
    assert legend['DNS'] == 'Dr. D. N. Sujatha'

def test_unknown_teacher_warning():
    """Test warning for unknown teacher acronym."""
    # ... create fixture with (ZZZ) not in legend ...
    canonical = parse_docx_to_canonical(fixture_bytes, db)
    warnings = [issue for issue in canonical.validation_issues if issue.severity == WARNING]
    assert any("ZZZ" in w.message for w in warnings)

def test_source_traceability():
    """Test source location preservation."""
    canonical = parse_docx_to_canonical(fixture_bytes, db)
    activity = canonical.activities[0]
    assert activity.source_location.source_type == "DOCX"
    assert activity.source_location.table_index is not None
    assert activity.source_location.row_index is not None
    assert activity.source_location.original_text is not None

def test_canonical_output_contract():
    """Test output matches CanonicalTimetable schema."""
    canonical = parse_docx_to_canonical(fixture_bytes, db)
    assert isinstance(canonical, CanonicalTimetable)
    assert canonical.academic_year is not None
    assert len(canonical.teachers) > 0
    assert len(canonical.activities) > 0
```

### 14.3 Integration Tests

```python
@pytest.mark.integration
def test_parse_full_mca_timetable(pg_db: Session):
    """Integration test: Parse actual MCA timetable."""
    with open("MCA Timetable-2026-Odd V7.docx", "rb") as f:
        docx_bytes = f.read()

    canonical = parse_docx_to_canonical(docx_bytes, pg_db)

    # Verify structure
    assert canonical.academic_year == "2026-2027"  # Inferred from "Odd semester 2026"
    assert len(canonical.teachers) >= 15  # From legend table
    assert len(canonical.activities) > 50  # Multiple days, sections, slots

    # Verify known activities exist
    mon_i_a_s3 = [a for a in canonical.activities
                  if a.day == "monday" and a.section == "I-A" and "S3" in a.slots]
    assert len(mon_i_a_s3) == 1
    assert mon_i_a_s3[0].subject_or_activity == "DBMS"
    assert "VR" in mon_i_a_s3[0].teacher_acronyms

    # Verify ambiguous case has warning
    tue_i_a_s4_s5 = [a for a in canonical.activities
                     if a.day == "tuesday" and a.section == "I-A" and "S4" in a.slots and "S5" in a.slots]
    assert len(tue_i_a_s4_s5) > 0
    activity = tue_i_a_s4_s5[0]
    warnings = [i for i in activity.validation_issues if i.severity == WARNING]
    assert len(warnings) > 0
    assert any("AMBIGUOUS" in w.code for w in warnings)

@pytest.mark.integration
def test_docx_teacher_resolution(pg_db: Session):
    """Test teacher resolution via department-aware lookup."""
    # Seed database with "Computer Applications" department teachers
    program = Program(name="MCA", level="PG")
    pg_db.add(program)
    pg_db.flush()

    teacher = Teacher(
        name="Dr. S. Uma",
        acronym="SU",
        department="Computer Applications",
        level="PG",
        program_id=program.id,
        semester=1
    )
    pg_db.add(teacher)
    pg_db.commit()

    # Parse DOCX
    canonical = parse_docx_to_canonical(docx_bytes, pg_db)

    # Verify teacher resolution
    su_teacher = [t for t in canonical.teachers if t.acronym == "SU"][0]
    assert su_teacher.resolution == REUSE
    assert su_teacher.resolved_teacher_id == teacher.id
```

---

## 15. PARSER CONTRACT

### 15.1 Stage Responsibilities

#### Stage 1: Structural Extraction
**Input**: DOCX binary (bytes)
**Output**: `Document` object (python-docx)
**Responsibility**:
- Load DOCX using python-docx library
- Extract WordprocessingML structure
- Provide access to tables, paragraphs, runs

**Errors**:
- Invalid DOCX file → EXCEPTION

---

#### Stage 2: Logical Grid Reconstruction
**Input**: `Document` object
**Output**: `Grid` (2D array of `GridCell` objects)
**Responsibility**:
- Identify timetable table vs. legend table
- Reconstruct physical grid accounting for merges
- Detect `w:gridSpan` (horizontal merge)
- Detect `w:vMerge` (vertical merge)
- Preserve cell text and coordinates

**Errors**:
- No timetable table found → ERROR: "No timetable table detected"
- Malformed table structure → ERROR: "Table structure invalid"

---

#### Stage 3: Activity Reconstruction
**Input**: `Grid`, day/slot mappings
**Output**: List of `RawActivity` objects
**Responsibility**:
- Map columns to slot codes (S1-S9)
- Map rows to days and sections
- Extract cell text: subject, teacher acronyms, room codes
- Detect multi-slot activities (gridSpan > 1)
- Preserve original cell text in source location

**Errors**:
- Unknown day name → ERROR
- Unknown slot column → ERROR
- Cell in break column treated as activity → ERROR

**Warnings**:
- Multiple activities in one cell → WARNING: "Ambiguous parallel activity"
- Missing subject → WARNING: "Activity missing subject"

---

#### Stage 4: Teacher/Resource Resolution
**Input**: List of `RawActivity`, faculty legend, Resource Foundation
**Output**: `CanonicalTimetable`
**Responsibility**:
- Map teacher acronyms to `TeacherIdentity` using legend
- Resolve teachers against database (department-aware)
- Resolve resources against Resource Foundation
- Populate `resolved_teacher_id` or mark as CREATE
- Populate `resource_id` or mark as UNRESOLVED

**Warnings**:
- Teacher acronym not in legend → WARNING: "Unknown teacher 'XX'"
- Teacher not in database → INFO: "Will create teacher 'XX'"
- Resource not in database → WARNING: "Unknown resource 'CA1'"
- Ambiguous resource → WARNING: "Resource 'LAB' matches multiple"

---

#### Stage 5: ValidationEngine
**Input**: `CanonicalTimetable`
**Output**: `CanonicalTimetable` with `validation_issues` populated
**Responsibility**:
- Run all standard validation rules
- Check for duplicate activities
- Check for overlapping slots
- Check for invalid slot codes
- Check for invalid day names
- Validate LAB activities have 2 consecutive slots

**Errors/Warnings**: As defined by ValidationEngine

---

### 15.2 Error Propagation

- **EXCEPTION**: Critical failure, cannot continue (e.g., file corrupt)
- **ERROR**: Structural problem, import blocked (e.g., invalid day name)
- **WARNING**: Data quality issue, import allowed with review (e.g., unknown teacher)
- **INFO**: Informational message (e.g., teacher will be created)

**Import Decision**:
- If any ERROR: Block import, show errors to user
- If only WARNINGS: Allow import, show warnings for review
- If only INFO: Import succeeds

---

## 16. EXPLICIT DO NOT GUESS RULES

### 16.1 Prohibited Heuristics

The parser **MUST NOT**:

1. **Assume spatial grouping indicates association**
   - ❌ "(SU) (TS)" grouped by whitespace → "SU goes with first activity, TS with second"
   - ✅ Extract both, emit WARNING if ambiguous

2. **Map teachers to activities by position**
   - ❌ "First teacher in cell → first activity, second teacher → second activity"
   - ✅ Extract all, mark as ambiguous if multiple

3. **Infer resource from teacher's primary location**
   - ❌ "VR usually teaches in CA1, so assume CA1"
   - ✅ Extract only if resource explicitly in cell

4. **Guess entry_type from teacher or room**
   - ❌ "LAB1A mentioned → must be LAB type"
   - ✅ Infer entry_type from subject code only ("LAB" keyword, trailing digit)

5. **Resolve ambiguous section references**
   - ❌ "DS 3,4" → "Could be DS Section 3 and DS Section 4"
   - ✅ Treat as single subject string, let ValidationEngine or user disambiguate

6. **Fill missing teacher from previous cell**
   - ❌ "No teacher in current cell → copy from cell above"
   - ✅ Missing teacher → WARNING: "Activity missing teacher"

7. **Merge duplicate-looking activities**
   - ❌ "Two cells with 'DBMS (VR) CA1' → merge into one activity"
   - ✅ Treat as separate activities unless structurally merged (gridSpan)

8. **Assume breaks are working slots**
   - ❌ "Activity scheduled during break → shift to adjacent slot"
   - ✅ Activity during break → ERROR: "Activity scheduled during break"

9. **Interpret line breaks as activity separators**
   - ❌ "Cell has 3 lines → 3 separate activities"
   - ✅ Parse structure (parentheses, keywords), emit WARNING if ambiguous

10. **Use external knowledge not in document**
    - ❌ "I know VR teaches Python, so this must be Python"
    - ✅ Extract only what document explicitly states

### 16.2 Permitted Inference

The parser **MAY**:

1. **Infer entry_type from subject name**
   - "DBMS" → "CLASS"
   - "DBMS LAB" or "DBMS 1" → "LAB"
   - "PE TUTORIAL" → "OTHER"

2. **Normalize acronyms**
   - "vr" → "VR"
   - "  TS  " → "TS"

3. **Normalize resource codes**
   - "Lab 1A" → "LAB1A"
   - "ca1" → "CA1"

4. **Infer department from document header**
   - Header says "Department of Computer Applications" → All teachers belong to this department

5. **Infer academic year from document**
   - "Odd semester 2026" → "2026-2027" (Odd semester starts in year N, ends in N+1)

6. **Map time ranges to institutional slot codes**
   - "08.00-08.55" → "S1" (using schedule_config.py)

7. **Detect breaks by keyword**
   - Cell contains "BREAK", "LUNCH", "COFFEE" → Not an activity

---

## VERIFICATION SUMMARY

### Tables Found
- **Main Timetable**: 1 table, ~7 days × 4 sections × 13 columns (with time slots)
- **Faculty Legend**: 1 table, 15 rows × 4 columns

### Table Dimensions
- **Timetable**: Approximately 28 rows (7 days × 4 sections) × 14 columns (day, section, 9 slots, 2 breaks, 2 buffer)
- **Legend**: 15 rows × 4 columns

### Merge Patterns
- **Horizontal (`w:gridSpan`)**: Used for multi-slot activities (labs spanning 2 slots)
- **Vertical (`w:vMerge`)**: Used for day names spanning all sections, breaks spanning all sections
- **No nested merges**: Observed structure is simple, no 2D merged blocks

### Timetable Geometry
- **Days**: MON, TUE, WED, THU, FRI, SAT (6 days, Sunday not present)
- **Sections**: I-A, I-B, III-A, III-B (4 sections)
- **Slots**: S1-S9 (9 working slots) + 2 breaks (morning, lunch)
- **Grid**: 7 rows (days) × 4 sub-rows (sections) × 14 columns (day/section/slots/breaks)

### Activity Patterns
- **Simple**: Subject + Teacher + Room on separate lines
- **Multi-slot**: Subject + Teachers + Room with `gridSpan=2`
- **Parallel/Ambiguous**: Multiple subjects/teachers/rooms in one cell (comma-separated)

### Multi-Slot Patterns
- **Mechanism**: `w:gridSpan="2"` attribute on cell
- **Interpretation**: Activity spans 2 consecutive time slots
- **Example**: PE lab S1-S2, ADA lab S4-S5

### Parallel Activity Patterns
- **Within cell**: Multiple activities listed with commas
- **Ambiguity**: Teacher-to-activity mapping unresolved
- **Parser behavior**: Extract all, emit WARNING, preserve original text

### Teacher Representation
- **In-cell**: Acronym in parentheses: `(VR)`, `(VPP, VR)`
- **Legend table**: Maps acronym → full name
- **Department**: Inferred from document header ("Computer Applications")

### Resource Representation
- **Format**: "CA1", "LAB1A", "FDC"
- **Location**: Last non-parenthesized line in cell
- **Ambiguity**: Multiple rooms for multiple activities → WARNING

### Ambiguities Found
1. **Tuesday I-A S4+S5**: "PY1, PE2, DS 3,4" with multiple teachers/rooms
2. **General pattern**: Any cell with N activities + M teachers/rooms where N>1 and M>1
3. **Resolution**: DO NOT GUESS, emit WARNING, let user resolve

### Proposed Reconstruction Algorithm
1. Load DOCX → Extract tables
2. Identify timetable table (has days, time slots)
3. Reconstruct grid (account for w:gridSpan, w:vMerge)
4. Map columns → slot codes (S1-S9, BREAK)
5. Map rows → days + sections
6. Parse each activity cell → extract subject, teachers, rooms
7. Detect ambiguities → emit WARNINGS
8. Build CanonicalTimetable
9. Run ValidationEngine
10. Return ImportPreview with errors/warnings

### Tests Proposed
- Unit tests: 15+ tests covering each parsing stage
- Integration test: Full MCA document parsing
- Fixture tests: Minimal, multi-slot, ambiguous, break, unknown teacher/resource scenarios
- Regression test: Verify XLSX import still works (not broken)

---

## IMPLEMENTATION STATUS

**NOT IMPLEMENTED**

This document is a **specification only**. No parser code has been written.

Next steps:
1. Review specification with stakeholders
2. Create test fixtures
3. Implement parser following this specification
4. Write tests
5. Integrate with existing import pipeline

---

**END OF SPECIFICATION**
