# Teacher Availability System — Shared Project Context

## 1. Project purpose

This is a four-day department-level prototype for teachers and the college Director. Teachers maintain their schedules; the Director searches for a teacher and immediately sees that teacher's daily/weekly schedule and derived free periods.

The prototype is intentionally scoped to one department, but the data model is designed so the project can later expand to the whole college.

## 2. Primary users

### Teacher

- Enter name and institutional acronym.
- Select UG or PG.
- Select academic program/branch (for example MCA, MBA, MTech).
- Select the semester being taught.
- Enter/edit a weekly timetable using predefined department time slots.
- Upload a timetable in Excel, DOCX, or PDF format.
- Review the parsed draft, correct mistakes, validate it, and confirm it.

### Director

- Search teachers by full/partial name or acronym.
- Search is case-insensitive and supports partial matching.
- Open a teacher profile.
- See today's schedule and current status.
- See free periods for the selected day.
- Switch days and view the complete week.

## 3. Core business idea

This is a timetable/availability system, not a generic calendar. The department has fixed time slots. Teachers are assigned to different slots. `FREE` is a derived state calculated from configured working slots minus occupied slots.

## 4. Department timetable rules

Default teaching slots:

- S1: 08:00–08:55
- S2: 08:55–09:50
- S3: 09:50–10:45
- Morning break: 10:45–11:15
- S4: 11:15–12:10
- S5: 12:10–13:05
- Lunch: 13:05–14:00
- S6: 14:00–14:55
- S7: 14:55–15:50
- S8: 15:50–16:45
- S9: 16:45–17:40

Each normal teaching slot is 55 minutes. The standard lab duration is 1 hour 50 minutes, represented by one logical lab entry linked to two consecutive configured slots.

Monday-Friday are the initial enabled working days. Saturday is supported as configurable rather than assumed active. Sunday is not a working day in the prototype.

Teachers do not enter arbitrary clock times for normal schedule entries; they select configured slots.

## 5. Recommended architecture

- Frontend: Next.js + TypeScript
- Backend: FastAPI + Python
- Database: PostgreSQL (Supabase is acceptable for the prototype)
- API: REST under `/api/v1`
- Excel parsing: `openpyxl`
- DOCX parsing: `python-docx`
- PDF parsing: `PyMuPDF`

All import formats must normalize to the same internal timetable representation. The parser is an input method, not the source of truth.

## 6. Final MVP database model

The executable schema is `database/schema.sql` and should be treated as the authoritative schema for a fresh database.

### `programs`

Academic program reference data: `id`, `name`, `level`, `is_active`.

### `teachers`

Faculty identity and teaching context: `id`, `name`, `acronym`, `level`, `program_id`, `semester`, `department`, `is_active`, timestamps.

The teacher-to-program relationship enforces matching UG/PG levels. Acronyms are unique after trimming and case normalization.

### `time_slots`

Fixed department scheduling configuration: `id`, `code`, `start_time`, `end_time`, `sequence`, `is_active`.

### `timetables`

A version of a teacher's schedule: `id`, `teacher_id`, `academic_year`, optional effective dates, `status`, `source`, `last_verified_at`, timestamps.

The MVP allows at most one confirmed timetable for a teacher in a given academic year; multiple drafts can exist during editing/import.

### `schedule_entries`

A logical occupied activity: `id`, `timetable_id`, `day_of_week`, `entry_type`, `subject_or_activity`, optional `section`, `room`, `notes`.

Entry types are `CLASS`, `LAB`, and `OTHER`.

### `schedule_entry_slots`

Join table between schedule entries and one or more configured time slots. This is required for labs spanning multiple periods.

The database also performs deferred validation to reject overlapping activities in a confirmed timetable for the same day and slot.

## 7. Why FREE is not stored

Never store `FREE` as a normal timetable row.

```text
enabled working slots
        -
occupied schedule-entry slots
        =
free slots
```

Break and lunch are fixed schedule configuration and are displayed separately. They are not reported as teacher free periods.

## 8. Manual data flow

```text
Teacher
  ↓
Profile information
  ↓
Timetable editor
  ↓
Draft timetable
  ↓
Validation
  ↓
Confirmation
  ↓
PostgreSQL
```

## 9. Import data flow

```text
Uploaded Excel/DOCX/PDF
  ↓
Format-specific parser
  ↓
Normalized timetable draft
  ↓
Teacher name/acronym matching
  ↓
Warnings + validation
  ↓
Editable preview
  ↓
Teacher correction
  ↓
Confirmation
  ↓
PostgreSQL
```

Imported data must never bypass review and write directly into confirmed timetable data.

A parser must not silently guess an ambiguous day, slot, teacher, or activity. Ambiguity becomes a warning for manual correction.

## 10. Required validation

At minimum:

- required/nonblank teacher identity fields
- valid UG/PG value
- valid program and matching level
- positive semester
- normalized acronym uniqueness
- valid day
- valid configured slot
- no duplicate slot occupancy in confirmed schedules
- valid consecutive slot usage for labs
- no invalid/unresolved import warnings at confirmation
- no more than one confirmed timetable per teacher per academic year

## 11. Director behavior

The Director's daily view should clearly show:

- teacher name and acronym
- program/semester context where useful
- each configured teaching slot
- break/lunch
- occupied activity
- derived free periods
- current status when current local time falls within a configured period
- a full-week option

The first answer the interface should make obvious is: **Is this teacher free, and when?**

## 12. Prototype non-goals

Do not add college-wide administration, mobile apps, notifications, leave management, room allocation, substitution workflows, attendance, SSO, WhatsApp integration, or a chatbot during this four-day prototype. These belong to later phases.

## 13. Two-person development model

Both developers work full-stack. Do not permanently divide frontend/backend/database ownership.

Each feature should be a vertical slice that may touch:

```text
Database → API → UI → Tests
```

Recommended workflow:

1. Create a focused feature branch.
2. Implement the feature end-to-end where practical.
3. Add tests.
4. Commit with a descriptive prefix.
5. Open a pull request.
6. Teammate reviews.
7. Merge only after tests/review pass.

`main` remains the stable integration branch.

## 14. Four-day delivery target

### Day 1 — Foundation + core vertical slices

Lock architecture/schema/API contracts and get teacher persistence plus Director search/schedule retrieval working end-to-end.

### Day 2 — Timetable engine + availability

Implement validation, labs, collision handling, free-time calculation, current status, and daily/weekly Director views.

### Day 3 — Import pipeline

Implement Excel, DOCX, PDF extraction, normalization, identity matching, warnings, editable import review, validation, and confirmation.

### Day 4 — QA + release

Freeze features. Run aggressive end-to-end and edge-case tests, fix defects, deploy, seed realistic demo data, and rehearse the Director demonstration.

## 15. Definition of success

```text
Teacher creates or uploads timetable
        ↓
System parses/normalizes
        ↓
Teacher reviews and edits
        ↓
Validation passes
        ↓
Timetable is confirmed in PostgreSQL
        ↓
Director searches name/acronym
        ↓
Director sees today's schedule + accurate free periods
        ↓
Director can view full week
```

## 16. Source-of-truth files

When there is a conflict, use this order:

1. `database/schema.sql`
2. `docs/DATA_MODEL.md`
3. `docs/TIMETABLE_RULES.md`
4. `docs/API.md`
5. other implementation files

Do not silently change the architecture or schema. Propose and document contract changes first.
