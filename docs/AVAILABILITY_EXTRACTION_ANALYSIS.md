# AVAILABILITY EXTRACTION ANALYSIS

## Executive Summary

The current DOCX parser is **unnecessarily blocking teacher/resource occupancy extraction** due to subject/activity tokenization ambiguity.

**KEY FINDING:** 24 unresolved blocks contain **deterministic teacher allocations** but are blocked only because activity phrases like "PY1, PE2, DS 3,4" cannot be tokenized into exact subject boundaries.

**AVAILABILITY REQUIREMENT:** We do NOT need to solve teacher→subject pairing. We only need to know:
- Which teachers are allocated to a block
- Which resources are allocated to a block
- Which slots they occupy

## Current Parser Statistics

**Real MCA DOCX (175 blocks):**
- ✅ Resolved: 78 blocks (44.6%)
- ⚠️ Unresolved: 97 blocks (55.4%)

**Unresolved Block Analysis:**
- 24 blocks: Teacher occupancy **CAN be extracted** (activity ambiguous, teacher deterministic)
- 73 blocks: Teacher occupancy **CANNOT be extracted** (teacher ambiguous/missing)

**Teacher Occupancy Currently Available:**
- From resolved blocks: 78 blocks → **282 slot occupancies**
- From unresolved but extractable: 24 blocks → **additional ~48 slot occupancies** (estimated)
- **Total extractable: ~330 slot occupancies** vs **currently used: 282**

## Critical Test Cases

### 1. Tuesday I-A S4+S5 — ACTIVITY AMBIGUOUS, TEACHERS DETERMINISTIC

**Original text:**
```
PY1, PE2,   DS 3, 4
(SU) (TS)  (SS, KPS)
(LAB1B)   (LAB 1A)
```

**Current parser status:** UNRESOLVED
- Activity candidates: `['PY1, PE2, DS 3, 4']` (tokenization ambiguous)
- Teacher candidates: `['SU', 'TS', 'SS', 'KPS']` ✅ DETERMINISTIC
- Resource candidates: `['LAB1B', 'LAB 1A']` ✅ DETERMINISTIC
- Slots: `['S4', 'S5']`
- Error code: `ACTIVITY_TOKENIZATION_AMBIGUOUS`

**Availability extraction capability:**
- ✅ SU: OCCUPIED Tuesday S4+S5
- ✅ TS: OCCUPIED Tuesday S4+S5
- ✅ SS: OCCUPIED Tuesday S4+S5
- ✅ KPS: OCCUPIED Tuesday S4+S5
- ✅ LAB1B: OCCUPIED Tuesday S4+S5
- ✅ LAB1A: OCCUPIED Tuesday S4+S5

**Currently blocked:** YES (due to activity tokenization)
**Should be blocked:** NO (teacher/resource allocation is deterministic)

### 2. Thursday III-B S2+S3 — ACTIVITY AMBIGUOUS, TEACHERS DETERMINISTIC

**Original text:**
```
ADA 1, DT 3,4
(TSP) (DNS, SU)
(Lab 1A)
```

**Current parser status:** UNRESOLVED
- Activity candidates: `['ADA 1, DT 3,4']` (tokenization ambiguous)
- Teacher candidates: `['TSP', 'DNS', 'SU']` ✅ DETERMINISTIC
- Resource candidates: `['Lab 1A']` ✅ DETERMINISTIC
- Slots: `['S2', 'S3']`
- Error code: `ACTIVITY_TOKENIZATION_AMBIGUOUS`

**Availability extraction capability:**
- ✅ TSP: OCCUPIED Thursday S2+S3
- ✅ DNS: OCCUPIED Thursday S2+S3
- ✅ SU: OCCUPIED Thursday S2+S3
- ✅ LAB1A: OCCUPIED Thursday S2+S3

**Currently blocked:** YES (due to activity tokenization)
**Should be blocked:** NO (teacher/resource allocation is deterministic)

### 3. Monday III-B Agile (DNS) S3 — RESOLVED

**Current parser status:** RESOLVED ✅
- Teacher: DNS
- Activity: Agile
- Slots: ['S3']
- **Availability: DNS OCCUPIED Monday S3** ✅

### 4. Saturday III-B S4+S5 — ACTIVITY AMBIGUOUS, TEACHERS DETERMINISTIC

**Pattern:** Same as Thursday case (ADA/DT with multiple teachers)
**Status:** Currently blocked by activity tokenization
**Should extract:** All teacher occupancies for S4+S5

## Current Architecture Issues

### Issue 1: Resolution Rule Too Strict for Availability

**File:** `backend/app/services/docx_import/resolution.py`

**Current rule (line ~15):**
```python
# A block is RESOLVED if and only if ALL of the following are true:
#
# 1. EXACTLY ONE ActivityCandidate exists
# 2. The ActivityCandidate is NOT marked as tokenization-ambiguous  ← BLOCKS AVAILABILITY
# 3. EXACTLY ONE TeacherCandidate exists
# 4. The TeacherCandidate identity is uniquely resolvable
# ...
```

**Problem:** Rule #2 blocks resolution even when teacher allocation is deterministic.

**For availability:** We don't care if "PY1, PE2, DS 3,4" is 1 activity or 4 activities. We only care that SU, TS, SS, KPS are all OCCUPIED.

### Issue 2: No Occupancy Extraction Path

**Current flow:**
```
TimetableBlock
    ↓
  Resolved? → NO (activity ambiguous)
    ↓
  Mark UNRESOLVED
    ↓
  Teacher occupancy LOST
```

**Needed flow:**
```
TimetableBlock
    ↓
  Can extract teacher/resource occupancy?
    ↓
  YES → Extract occupancy (even if activity ambiguous)
    ↓
  Store occupancy separately from activity resolution
```

## Teacher Occupancy That Can Be Safely Extracted

**Analysis of 24 blocks:**

All have error code `ACTIVITY_TOKENIZATION_AMBIGUOUS` but:
- Teacher candidates are present and deterministic
- Document structure clearly associates teachers with the block
- No guessing required

**Example teachers extractable from currently blocked blocks:**
- DNS: +4 additional occupancies
- SU: +6 additional occupancies
- TS: +4 additional occupancies
- SS: +6 additional occupancies
- KPS: +6 additional occupancies
- TSP: +4 additional occupancies

**Total impact:** ~48 additional slot occupancies (17% increase)

## Resource Occupancy That Can Be Safely Extracted

**Same 24 blocks also contain deterministic resource allocations:**
- LAB1A: Multiple additional occupancies
- LAB1B: Multiple additional occupancies
- Other labs/rooms

**No guessing required:** Document structure clearly shows resource allocation.

## Blocks Where Occupancy CANNOT Be Extracted

**73 blocks with genuine ambiguities:**

**Type 1: Multiple teachers, unclear allocation** (34 blocks)
- Error code: `AMBIGUOUS_TEACHER_MAPPING`
- Example: 3 teachers, 3 activities, no pairing structure
- **Correct behavior:** Cannot determine which teachers are allocated → mark UNKNOWN

**Type 2: Missing teacher** (26 blocks)
- Error code: `MISSING_TEACHER`
- Example: Activity shown but no teacher in parentheses
- **Correct behavior:** Cannot mark OCCUPIED or FREE → require manual input

**Type 3: Multiple activities, unclear teacher→activity pairing** (10 blocks)
- Error code: `AMBIGUOUS_BLOCK`
- Example: Multiple distinct activities, multiple teachers, no clear structure
- **Correct behavior:** Cannot determine allocation → require manual resolution

**Type 4: Ambiguous resource with single activity** (3 blocks)
- Error code: `AMBIGUOUS_RESOURCE_SINGLE_ACTIVITY`
- Example: Multiple resource candidates for one activity
- **Correct behavior:** Resource occupancy ambiguous → manual resolution

## Slot Duration Rules (Already Correct)

**Parser correctly handles multi-slot blocks:**
- S4+S5 → teacher occupied for BOTH slots (1h 50m)
- S2+S3 → teacher occupied for BOTH slots (1h 50m)
- Single slot → single occupancy (55m)

**Breaks correctly excluded:**
- 10:45-11:15 (Coffee break) → not a working slot
- 13:05-14:00 (Lunch break) → not a working slot

**Sunday:** Non-working day (not in MCA timetable)

## Recommended Implementation Changes

### Change 1: Separate Occupancy Extraction from Activity Resolution

**NEW CONCEPT:** `OccupancyExtractor`

**Responsibility:** Extract teacher/resource occupancy **independently** of activity tokenization.

**Logic:**
```python
def can_extract_occupancy(block: TimetableBlock) -> bool:
    """Check if teacher/resource occupancy can be deterministically extracted.

    Returns True if:
    - At least one teacher candidate exists
    - Teacher allocation is NOT genuinely ambiguous
    - Activity tokenization does NOT affect teacher allocation

    Returns False if:
    - No teachers present (MISSING_TEACHER)
    - Multiple teachers with unclear which are allocated (AMBIGUOUS_TEACHER_MAPPING)
    - Teacher identity unresolvable (UNRESOLVED_TEACHER_IDENTITY)
    """

    # Check teacher candidates
    if len(block.teacher_candidates) == 0:
        return False  # Cannot extract occupancy

    # Check for genuine teacher ambiguity
    error_codes = [i.code for i in block.issues if i.severity == ValidationSeverity.ERROR]

    if 'AMBIGUOUS_TEACHER_MAPPING' in error_codes:
        return False  # Genuinely ambiguous which teachers are allocated

    if 'MISSING_TEACHER' in error_codes:
        return False  # No teacher data

    if 'UNRESOLVED_TEACHER_IDENTITY' in error_codes:
        return False  # Cannot resolve teacher identity

    # Activity tokenization ambiguity does NOT prevent occupancy extraction
    if 'ACTIVITY_TOKENIZATION_AMBIGUOUS' in error_codes:
        # Teachers are still deterministically allocated to the block
        # We don't need to know which teacher → which activity
        return True  # CAN extract occupancy

    # All other cases: can extract
    return True


def extract_occupancy(block: TimetableBlock) -> list[TeacherOccupancy]:
    """Extract teacher occupancy from block.

    Returns list of (teacher, day, slots, status=OCCUPIED) tuples.
    Does NOT attempt to pair teachers with activities.
    """
    occupancies = []

    for teacher_candidate in block.teacher_candidates:
        occupancies.append(TeacherOccupancy(
            teacher_acronym=teacher_candidate.normalized_acronym,
            day=block.day,
            slots=block.slots,  # ALL slots in block
            status='OCCUPIED',
            source_block_id=block.temp_id
        ))

    return occupancies
```

### Change 2: Add OccupancyExtraction to Parser Output

**Update `DOCXImportPreview`:**

```python
class DOCXImportPreview:
    # ... existing fields ...

    # NEW: Occupancy data extracted independently
    teacher_occupancies: list[TeacherOccupancy]
    resource_occupancies: list[ResourceOccupancy]

    # Statistics
    occupancy_extraction_success_count: int
    occupancy_extraction_blocked_count: int
```

**Where used:**
- Availability API can directly use `teacher_occupancies`
- No need to wait for full activity resolution
- Manual resolution updates occupancies when finalized

### Change 3: Update Resolution Rule Documentation

**File:** `backend/app/services/docx_import/resolution.py`

**Add clarification:**

```python
class ResolutionRule:
    """AUTHORITATIVE RULE for determining if TimetableBlock is RESOLVED
    **FOR TIMETABLE IMPORT**.

    IMPORTANT: This determines if the block can be converted to a complete
    canonical timetable entry. This is SEPARATE from occupancy extraction.

    A block may be:
    - UNRESOLVED for timetable import (activity ambiguous)
    - BUT deterministic for occupancy extraction (teachers clearly allocated)

    See OccupancyExtractor for availability extraction rules.
    """
```

### Change 4: Add Occupancy Extraction Tests

**New test file:** `backend/tests/test_occupancy_extraction.py`

**Test cases:**
```python
def test_activity_ambiguous_teacher_deterministic_extracts_occupancy():
    """Tuesday I-A S4+S5: Activity ambiguous but all 4 teachers extractable."""

def test_multiple_teachers_unclear_mapping_blocks_occupancy():
    """3 activities, 3 teachers, no pairing → cannot extract."""

def test_missing_teacher_blocks_occupancy():
    """No teacher in parentheses → cannot extract."""

def test_multi_slot_occupancy_all_slots():
    """S4+S5 block → teacher occupied for BOTH slots."""

def test_breaks_not_working_slots():
    """Coffee/lunch breaks → not included in occupancy."""
```

## Minimum Implementation Changes

### Phase 1: Add Occupancy Extraction (No Breaking Changes)

1. **Create** `backend/app/services/docx_import/occupancy.py`
   - `OccupancyExtractor` class
   - `can_extract_occupancy()` function
   - `extract_teacher_occupancy()` function
   - `extract_resource_occupancy()` function

2. **Update** `backend/app/services/docx_import/staging.py`
   - Add `TeacherOccupancy` dataclass
   - Add `ResourceOccupancy` dataclass
   - Add occupancy fields to `DOCXImportPreview`

3. **Update** `backend/app/services/docx_import/parser.py`
   - Call `OccupancyExtractor` after classification
   - Populate occupancy fields in preview
   - **Do NOT change existing resolution logic**

4. **Add** `backend/tests/test_occupancy_extraction.py`
   - Test all occupancy extraction rules
   - Verify 24 blocks now extractable
   - Verify 73 blocks correctly blocked

### Phase 2: Occupancy API (Separate from Import)

**Later task:** Add availability endpoints that consume occupancy data

**DO NOT mix with timetable confirmation:** Occupancy is derived data, not canonical timetable entries.

## Impact Summary

### Before (Current)

- Resolved blocks: 78
- Teacher slot occupancies: 282
- Unresolved blocks: 97 (all teacher occupancy lost)

### After (With Occupancy Extraction)

- Resolved blocks: 78 (unchanged)
- Unresolved blocks: 97 (unchanged)
- **Teacher slot occupancies: ~330 (+17%)**
- **24 blocks contribute occupancy despite activity ambiguity**

### Accuracy

- **No guessing added:** Only extracts deterministic allocations
- **Safety preserved:** Genuinely ambiguous blocks remain UNKNOWN
- **Availability requirement met:** Teacher/resource occupancy extractable independently of subject tokenization

## Test Suite Verification

**Current status:**
```bash
pytest backend/tests -q
295 passed, 8 skipped, 0 failed
```

**After implementation:**
```bash
pytest backend/tests -q
[Expected: 320+ passed, 8 skipped, 0 failed]
```

**New tests added:** ~25 tests for occupancy extraction

## Conclusion

**READY FOR IMPLEMENTATION:** Minimum changes identified, no architecture redesign needed.

**Impact:** 17% increase in extractable teacher occupancy data without adding any guessing or reducing safety.

**Next step:** Implement Phase 1 (occupancy extraction) before building availability API.

---

**Generated:** 2026-09-10
**Document version:** 1.0
**Status:** Analysis complete, implementation not started
