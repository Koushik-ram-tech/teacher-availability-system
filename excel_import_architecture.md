# Teacher Availability System — Excel-First Import Architecture

Status: design only. No code changes. No schema changes. Schema (`programs`,
`teachers`, `time_slots`, `timetables`, `schedule_entries`,
`schedule_entry_slots`) remains exactly as defined in `database/schema.sql`.

This document supersedes only the *primary workflow* described in
`PROJECT_CONTEXT.md` §2 ("Teacher enters/edits a weekly timetable using
predefined slots" is now the fallback path, not the main path). Everything
else in `PROJECT_CONTEXT.md`, `TIMETABLE_RULES.md`, `DATA_MODEL.md`, and
`API.md` still applies unless explicitly noted in section K below.

---

## A. Recommended final Excel template

One workbook, three sheets, fixed sheet names (case-insensitive match, but
document the canonical casing):

1. `Teachers`
2. `Schedule`
3. `Metadata`

Rules for the workbook as a whole:

- Row 1 of every sheet is a header row with the exact column names in
  section B. The parser matches headers case-insensitively and trims
  whitespace, but does not guess renamed/missing columns.
- One workbook = one academic year (declared once, in `Metadata`).
- One workbook may contain many teachers and many schedule rows.
- Empty trailing rows are ignored. A row is "present" if any cell in it is
  non-blank.
- No merged cells, no formulas expected in data cells (parser reads
  computed values only, does not evaluate formulas itself).

## B. Exact columns and allowed values

### `Teachers` sheet

| Column | Required | Type | Allowed values |
|---|---|---|---|
| `name` | Yes | text | non-blank |
| `acronym` | Yes | text | non-blank; case-insensitive unique across the sheet and against existing DB rows (excluding the same teacher being updated) |
| `level` | Yes | text | `UG` or `PG` (case-insensitive, normalized to upper) |
| `program` | Yes | text | must match an existing `programs.name` (case-insensitive) whose `level` equals this row's `level` |
| `semester` | Yes | integer | positive integer |
| `department` | No | text | defaults to `Prototype Department` if blank, matching the DB default |

A teacher row may refer to a **new** teacher (create) or an **existing**
teacher identified by normalized acronym (update profile fields). This
sheet never touches timetable data.

### `Schedule` sheet

| Column | Required | Type | Allowed values |
|---|---|---|---|
| `teacher_acronym` | Yes | text | must resolve to a teacher, either already in DB or created earlier in this same `Teachers` sheet |
| `day` | Yes | text | one of `monday`…`saturday` (case-insensitive); `sunday` rejected |
| `type` | Yes | text | `CLASS`, `LAB`, `OTHER` (case-insensitive, normalized to upper) |
| `slots` | Yes | text | one or more slot codes from `S1`–`S9`, delimited (see below) |
| `subject_or_activity` | No | text | free text, max 500 chars; blank allowed |
| `section` | No | text | free text, max 100 chars |
| `room` | No | text | free text, max 100 chars |
| `notes` | No | text | free text, max 1000 chars |

**`slots` delimiter format** — the workbook author should be able to write
either:
- Two separate slot codes joined with `+` for a lab: `S6+S7`
- A single slot code for a normal class: `S3`

The parser accepts `+`, `,`, `/`, or whitespace as equivalent delimiters,
normalizes to a list, then de-duplicates. This is a parsing convenience
only — validation rules (below) still apply strictly after normalization.

- `type = LAB` → `slots` must normalize to exactly 2 codes, and that pair
  must be in `VALID_LAB_PAIRS` from `schedule_config.py`
  (`{S1,S2},{S2,S3},{S4,S5},{S6,S7},{S7,S8},{S8,S9}`).
- `type = CLASS` or `OTHER` → `slots` must normalize to exactly 1 code.
  (The MVP data model ties one `schedule_entries` row to one `day_of_week`;
  a non-LAB entry spanning multiple slots is out of scope — same
  restriction the manual editor already has.)

### `Metadata` sheet

| Column | Required | Type | Allowed values |
|---|---|---|---|
| `academic_year` | Yes | text | `YYYY-YYYY`, second year = first + 1 (same rule as `is_valid_academic_year`) |

Exactly one data row. This is the single source of the academic year for
every teacher/schedule row in the workbook — there is no per-row academic
year and no inference from "current date."

## C. Example workbook contents

**`Metadata`**

| academic_year |
|---|
| 2025-2026 |

**`Teachers`**

| name | acronym | level | program | semester | department |
|---|---|---|---|---|---|
| Koushik Rao | KR | PG | MCA | 3 | Prototype Department |
| Anita Sharma | AS | PG | MCA | 3 | Prototype Department |

**`Schedule`**

| teacher_acronym | day | type | slots | subject_or_activity | section | room | notes |
|---|---|---|---|---|---|---|---|
| KR | monday | CLASS | S1 | Database Management Systems | MCA-A | 204 | |
| KR | monday | LAB | S6+S7 | DBMS Lab | | Lab 3 | |
| KR | tuesday | CLASS | S2 | Operating Systems | MCA-A | 204 | |
| AS | monday | CLASS | S1 | Software Engineering | MCA-B | 210 | |
| AS | wednesday | LAB | S4+S5 | SE Lab | | Lab 1 | |
| AS | wednesday | OTHER | S9 | Department Meeting | | | Optional attendance |

## D. Normalized JSON structure produced by parser

The parser's output is an in-memory (and API-transportable) draft object.
It never writes to the DB directly. Shape:

```json
{
  "academic_year": "2025-2026",
  "teachers": [
    {
      "row_ref": "Teachers!2",
      "action": "CREATE",
      "name": "Koushik Rao",
      "acronym": "KR",
      "level": "PG",
      "program_name": "MCA",
      "semester": 3,
      "department": "Prototype Department",
      "resolved_teacher_id": null,
      "warnings": []
    }
  ],
  "days": {
    "monday": [
      {
        "row_refs": ["Schedule!2"],
        "teacher_acronym": "KR",
        "resolved_teacher_ref": "KR",
        "slot_ids": ["S1"],
        "entry_type": "CLASS",
        "subject_or_activity": "Database Management Systems",
        "section": "MCA-A",
        "room": "204",
        "notes": null,
        "warnings": []
      },
      {
        "row_refs": ["Schedule!3"],
        "teacher_acronym": "KR",
        "resolved_teacher_ref": "KR",
        "slot_ids": ["S6", "S7"],
        "entry_type": "LAB",
        "subject_or_activity": "DBMS Lab",
        "section": null,
        "room": "Lab 3",
        "notes": null,
        "warnings": []
      }
    ]
  },
  "warnings": [],
  "errors": []
}
```

Key design points:

- The structure is **grouped by teacher inside each day** at persistence
  time, but the parser's flat `days` output keeps every row traceable back
  to `row_refs` (sheet name + row number) for the preview UI to highlight
  exactly which Excel cell a problem came from.
- `warnings` are non-fatal, resolvable-in-preview issues (e.g. ambiguous
  program match, section left blank). `errors` are fatal — the row cannot
  be imported until fixed, and the workbook-level `errors` array blocks
  confirmation entirely.
- This is exactly the payload shape the existing `TimetableWriteIn` /
  `ScheduleEntryIn` pydantic schemas already expect per teacher — the
  import pipeline's final normalized output, once split per teacher, maps
  onto the current `POST/PUT /teachers/{id}/timetable` contract with no
  schema changes.

## E. Validation rules

Applied in layered order — cheaper/structural checks first, so a malformed
workbook fails fast before wasting time on cross-row checks.

**1. Workbook structure**
- All three sheets present.
- Header rows match expected columns (missing required column = fatal,
  reject the whole workbook before row-level parsing).

**2. Per-row syntactic validation (`Teachers` sheet)**
- Required fields non-blank.
- `level` ∈ {UG, PG}.
- `semester` is a positive integer.
- `acronym` unique within the sheet (case-insensitive, trimmed).

**3. Per-row syntactic validation (`Schedule` sheet)**
- Required fields non-blank.
- `day` ∈ valid day names (Mon–Sat).
- `type` ∈ {CLASS, LAB, OTHER}.
- `slots` parses to known codes `S1`–`S9` only.

**4. Per-row semantic validation (`Schedule` sheet)**
- LAB ⇒ exactly 2 slots, and pair ∈ `VALID_LAB_PAIRS`.
- CLASS/OTHER ⇒ exactly 1 slot.

**5. Cross-sheet resolution**
- Every `Schedule.teacher_acronym` resolves to a teacher either created in
  `Teachers` (this workbook) or already `is_active` in the DB. Unresolvable
  → fatal error on that schedule row (not a silent drop).
- Every `Teachers.program` + `level` resolves to an existing active
  `programs` row. Unresolvable → fatal error on that teacher row.

**6. Cross-row consistency (per teacher, per day)**
- No two rows for the same teacher/day claim the same slot code (mirrors
  `no_duplicate_slots_per_day`). Conflict → fatal, both rows flagged.

**7. Metadata validation**
- Exactly one `Metadata` row.
- `academic_year` matches `YYYY-YYYY` with consecutive years.

**8. Import-level ambiguity → warning, not guess**
- Same rule as the existing import philosophy in `PROJECT_CONTEXT.md` §9 /
  `TIMETABLE_RULES.md` "Import rule": the parser never invents a slot,
  day, or teacher match. Fuzzy/partial name matches (e.g. acronym differs
  only by case/whitespace and already normalizes to a match) are auto-
  resolved because normalization is deterministic; genuinely ambiguous
  cases (e.g. two DB teachers whose normalized acronyms collide — should
  be impossible given the unique index, but a defensive check stays) are
  surfaced as warnings requiring manual resolution in preview.

**9. Confirmation-time re-validation**
- Everything above is re-checked at confirmation time against current DB
  state (not just at initial parse time), because DB state can change
  between upload and confirmation (e.g. another import created a
  conflicting draft). This reuses the same DB-level deferred triggers that
  already protect confirmed timetables.

## F. Import workflow

```
1. Upload
   POST multipart Excel file
   → stored temporarily, not yet parsed into DB

2. Parse
   openpyxl reads all three sheets
   → raw rows → normalized draft object (Section D)
   → syntactic validation (E.1–E.4) applied inline during parsing

3. Resolve
   → cross-sheet/cross-row validation (E.5–E.7)
   → produces warnings[] and errors[]

4. Return import preview
   → normalized draft + warnings + errors returned to frontend
   → NOTHING written to DB yet

5. Editable preview (frontend)
   → teacher/admin reviews flagged rows
   → can edit values inline (same shape as manual entry forms)
   → re-validated client-side + re-submitted for server-side re-check
     (loop back to step 3 as needed)

6. Confirm import
   → only allowed when errors[] is empty (warnings may be acknowledged
     but must be explicitly resolved/dismissed per row — no "confirm with
     open warnings")
   → triggers step 7

7. Transactional persistence
   → for each new teacher: create teacher row (or update existing)
   → for each affected (teacher, academic_year): create or replace the
     DRAFT timetable (reuse existing POST/PUT semantics — an import never
     writes directly to a CONFIRMED timetable)
   → all-or-nothing per workbook: if any teacher/day fails to persist,
     the whole import transaction rolls back and reports which row(s)
     caused the failure
   → result status: DRAFT timetables created/updated

8. Manual confirmation step (unchanged)
   → teacher (or authorized reviewer) still explicitly confirms each
     DRAFT → CONFIRMED, exactly as in the manual-entry flow
   → import does NOT auto-confirm, even with zero warnings
```

This preserves the mandated separation: **parsing → validation →
preview/edit → confirmation → persistence** are five distinct stages, and
DRAFT→CONFIRMED remains a separate, explicit, human action after import.

## G. Required API endpoints

All new endpoints live under the existing `/api/v1` prefix and read/write
only through the existing teacher/timetable tables — no schema change.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/imports/excel` | Upload workbook; returns parsed+validated draft (Section D shape) with a server-side `import_id`. No DB writes. |
| `GET` | `/api/v1/imports/{import_id}` | Re-fetch the current state of an in-progress import preview (in case the frontend reloads). |
| `PATCH` | `/api/v1/imports/{import_id}` | Submit corrected rows from the preview UI; server re-runs validation (Section E) and returns updated warnings/errors. |
| `POST` | `/api/v1/imports/{import_id}/confirm` | Only callable when `errors` is empty; performs the transactional persistence in Section F step 7. Creates/updates teachers and writes/replaces DRAFT timetables. Returns per-teacher DRAFT timetable IDs. |
| `DELETE` | `/api/v1/imports/{import_id}` | Discard an in-progress import (cleanup; no DB rows touched since nothing was persisted). |

Notes:
- Existing endpoints (`POST/PUT /teachers/{id}/timetable`, teacher CRUD,
  Director search/read) are unchanged and still used — for example, the
  import's step 7 persistence internally reuses the exact same write path
  the manual editor uses, so behavior stays consistent between the two
  entry methods.
- `import_id` scope is short-lived (e.g. an in-memory or a scratch table
  keyed by UUID + TTL) — it is a staging concept, not a new permanent
  domain table. If persistence needs it to survive a server restart, a
  minimal `import_batches` staging table could be considered, but this is
  a decision to make explicitly later, not assumed now (see Section K).

## H. Required backend/frontend files

**Backend (new)**
- `app/services/excel_import/parser.py` — reads workbook, sheet-by-sheet,
  emits raw rows.
- `app/services/excel_import/normalizer.py` — raw rows → normalized draft
  object (Section D), applies delimiter normalization for `slots`.
- `app/services/excel_import/validator.py` — Section E rules; pure
  functions over the normalized draft, no I/O except DB lookups for
  cross-sheet resolution (teacher acronyms, program names).
- `app/services/excel_import/persister.py` — step 7: transactional
  create/update of teachers + DRAFT timetables, reusing the existing
  timetable write service/schemas rather than duplicating logic.
- `app/api/v1/imports.py` — the 5 endpoints in Section G.
- `app/schemas/imports.py` — pydantic models for the draft/preview
  payload, warnings, errors (mirrors Section D).

**Backend (touched, read-only reuse, no modification expected)**
- `app/core/schedule_config.py` — reused for `SLOT_CODES`,
  `VALID_LAB_PAIRS`, `VALID_DAY_NAMES`, `is_valid_academic_year`.
- `app/schemas/timetable.py` — reused for `ScheduleEntryIn` /
  `TimetableWriteIn` so import persistence goes through identical
  validation to manual entry.

**Frontend (new)**
- `src/pages/ImportExcel.tsx` — upload screen.
- `src/pages/ImportPreview.tsx` — editable preview grid (teachers table +
  per-day schedule table), row-level warning/error badges.
- `src/api/imports.ts` — client functions for the 5 endpoints (mirrors
  `api.ts` conventions already in the project).
- `src/types/imports.ts` — TS types mirroring Section D's normalized
  shape.

**Frontend (touched, reused)**
- `src/types.ts` — `ScheduleEntryPayload`, `DayName`, `EntryType`,
  `isValidLabPair`, `isValidAcademicYear` all reused as-is by the import
  preview code; no changes needed.

## I. Error-handling strategy

- **Workbook-level fatal errors** (missing sheet, missing required
  column, unreadable/corrupt file, wrong file type): reject at `POST
  /imports/excel` with `400`, before any row parsing. No `import_id` is
  created.
- **Row-level errors** (Section E fatal checks): the workbook is still
  parsed as far as possible; every row gets an individual status
  (`OK` / `WARNING` / `ERROR`) so the preview UI can show good rows as
  ready and bad rows as needing correction, instead of failing the entire
  upload on one bad cell.
- **Row-level warnings**: surfaced but non-blocking for parsing; blocking
  for confirmation until explicitly resolved.
- **Confirmation-time errors**: distinguished from parse-time errors —
  e.g. a DB constraint violation caught at persistence (step 7) is
  reported per-teacher/per-day, and the whole confirm transaction rolls
  back (no partial import). Reuse existing HTTP status conventions from
  `API.md`: `400` malformed request, `409` conflicting DRAFT/CONFIRMED
  state, `422` semantic validation failure, `500` unexpected.
- **Idempotency / re-upload**: re-uploading a corrected workbook for the
  same academic year does not duplicate teachers (acronym-based
  create-or-update) and replaces the DRAFT timetable content for affected
  teachers atomically (same "PUT replaces DRAFT" semantics already in
  `API.md`), never touching CONFIRMED data.
- **Partial teacher failure isolation**: if teacher A's rows are all valid
  and teacher B's rows have an unresolved error, the design should still
  let A's DRAFT import proceed independently of B if the user chooses
  (configurable at confirm-time: confirm all valid teachers now, leave B
  for correction) — but the default/simplest Day-3 behavior can be
  all-or-nothing for the whole workbook if time is tight. Flag this as an
  explicit scope decision to make before implementation (see Section L).

## J. Exact edge cases for Excel import

1. **Blank optional columns** — `subject_or_activity`/`section`/`room`/
   `notes` empty → stored as `null`; DB fallback (`entry_type` as label)
   already handled by existing `effective_subject` logic, reused as-is.
2. **Whitespace/case variance** in `acronym`, `day`, `type`, `program` —
   normalized before comparison (trim + case-fold), consistent with the
   DB's own normalized unique index on acronym.
3. **Duplicate teacher row in `Teachers` sheet** (same acronym twice) —
   fatal error, both rows flagged, workbook not confirmable until fixed.
4. **`Schedule` row referencing a teacher not in `Teachers` and not in
   DB** — fatal error on that schedule row only; does not block unrelated
   teachers' rows.
5. **LAB with slots given as `S7+S6` (reversed order)** — normalize by
   sorting before pair-membership check; order in the sheet must not
   matter.
6. **LAB with non-consecutive or break-spanning slots** (e.g. `S3+S4`,
   `S5+S6`) — fatal, exact same rule as manual entry.
7. **LAB with 1 or 3+ slots** — fatal, "a LAB must occupy exactly two
   consecutive slots."
8. **CLASS/OTHER with 2+ slots listed** — fatal (multi-slot non-lab
   entries are out of scope for MVP, same as manual editor).
9. **Same teacher, same day, overlapping slot claimed by two rows** —
   fatal, both rows flagged (mirrors `no_duplicate_slots_per_day`).
10. **`day = sunday`** — fatal, Sunday is never a working day in this
    prototype.
11. **Unknown slot code** (e.g. `S10`, typo `S1O`) — fatal, list valid
    codes in the error message.
12. **Multiple `Metadata` rows, or conflicting academic years across
    rows** — fatal at the workbook level; reject before row parsing.
13. **Extra/unexpected columns in any sheet** — ignored (forward
    compatible), but a warning is logged/surfaced so a typo'd column
    header doesn't silently get dropped without the user noticing.
14. **Extra unexpected sheets** — ignored.
15. **Row where a cell contains only whitespace** — treated as blank, not
    as a distinct value ("  " ≠ a valid acronym).
16. **Numeric-looking `semester` stored as text** (Excel quirk, e.g.
    `"3 "` or `3.0`) — coerced to int where unambiguous; reject if not
    coercible to a positive integer.
17. **`program` value matches a program name that exists in *both* UG and
    PG for a different level than the row's own `level`** — must resolve
    using the `(name, level)` pair, not name alone; mismatch between
    stated `level` and the program actually found at that level → fatal
    (mirrors the DB's own `programs_id_level_unique` / teacher FK
    constraint).
18. **Re-importing the same workbook twice unchanged** — should be a
    no-op / idempotent DRAFT replace, not duplicate entries.
19. **Very large workbook** (many teachers/rows) — parser should not load
    the whole thing into memory in a way that blocks the request
    indefinitely; acceptable to defer streaming/async processing given
    the 4-day prototype scope, but the interface (`import_id` + polling
    via `GET /imports/{id}`) already anticipates this if needed later.
20. **File is not actually `.xlsx`** (wrong extension, corrupted, `.xls`
    binary format, CSV renamed to xlsx) — reject at upload with a clear
    `400`, not a cryptic parser stack trace.

## K. What should remain untouched in the current Day-1 implementation

- **Database schema** (`schema.sql`) — no changes. Import writes through
  the same tables via the same DRAFT/CONFIRMED lifecycle and the same
  deferred-trigger occupancy validation.
- **`schedule_config.py`** — `SLOT_CODES`, `VALID_LAB_PAIRS`,
  `DAY_NAME_TO_ISO`, `is_valid_academic_year` are reused verbatim by the
  import validator; do not fork or duplicate this logic in the import
  service.
- **`timetable.py` pydantic schemas** (`ScheduleEntryIn`,
  `TimetableWriteIn`, and their validators) — the import persister should
  construct/reuse these schemas for the final DB write rather than writing
  raw SQL, so both manual and imported data go through identical
  validation at the point of persistence.
- **Existing `POST/PUT /teachers/{id}/timetable` endpoints and their
  409/404 semantics** — unchanged; the import persister calls into the
  same underlying service logic (not the HTTP layer necessarily, but the
  same semantics) rather than reimplementing draft-replace behavior.
- **Director-facing read endpoints and CONFIRMED-only semantics** —
  unchanged; import never writes CONFIRMED data.
- **Frontend manual timetable editor** — stays as the fallback/secondary
  path for corrections and for teachers without a workbook entry; not
  removed.
- **`types.ts` helpers** (`isValidLabPair`, `isValidAcademicYear`,
  `entryLabel`, `applyEntriesToPeriods`, etc.) — reused by the import
  preview UI as-is.
- **API versioning and error-status conventions** in `API.md` — the new
  import endpoints follow the same `400/404/409/422/500` contract rather
  than inventing new status semantics.

## L. Recommended implementation order for a one-day deadline

Given this is layered on top of an already-decided Day 1/2 plan and must
fit in a compressed timeline, suggested order (roughly morning → evening):

1. **Freeze the template** (Sections A–C) and write the seed example
   workbook now — this unblocks both backend and frontend work in
   parallel and gives QA a fixture immediately.
2. **Parser + normalizer** (raw Excel → Section D draft object), unit
   tested directly against the example workbook — no API, no DB yet.
3. **Validator** (Section E rules) as pure functions over the draft
   object, unit tested against the edge cases in Section J — still no
   API/DB.
4. **`POST /imports/excel`** wiring parser+normalizer+validator together,
   returning the draft+warnings+errors. This is the first end-to-end
   testable slice (upload → see structured JSON back).
5. **Persister** (`confirm` step), reusing existing `ScheduleEntryIn` /
   `TimetableWriteIn` and existing draft-replace logic — persists to
   DRAFT only, transactionally.
6. **`POST /imports/{id}/confirm`** + **`DELETE /imports/{id}`**, plus
   minimal in-memory/staging storage for `import_id`.
7. **Frontend upload screen** — file picker + call to
   `POST /imports/excel`, render raw warnings/errors as a flat list first
   (no fancy grid yet) so the pipeline is demoable early.
8. **Frontend editable preview grid** — upgrade step 7's flat list into
   the row-level editable table with per-row status badges; wire
   `PATCH /imports/{id}` for corrections.
9. **`GET /imports/{id}`** for reload/resume support — lowest priority;
   skip first if time runs out, since the happy path (upload → preview →
   confirm in one sitting) doesn't strictly need it.
10. **End-to-end rehearsal** using the example workbook from step 1: fresh
    DB → upload → preview → confirm → DRAFT created → manually flip to
    CONFIRMED → Director search shows the imported schedule. This is the
    actual demo script and should be run well before the deadline, not
    only at the end.

Explicit scope decisions to make *before* coding starts (flagged in
Sections I and G):
- Whether partial-teacher confirmation (Section I, "partial teacher
  failure isolation") is in scope, or whether confirm is strictly
  all-or-nothing for the whole workbook. All-or-nothing is simpler and
  recommended given the timeline.
- Whether `import_id` staging needs to survive a server restart (a real
  staging table) or can be in-memory for the demo. In-memory is
  recommended given the timeline.
