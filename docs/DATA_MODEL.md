# Data Model

This document defines the MVP database contract. `database/schema.sql` is the executable source of truth for a fresh PostgreSQL/Supabase database.

## Relationship overview

```text
programs
   │
   └──< teachers
          │
          └──< timetables
                 │
                 └──< schedule_entries
                         │
                         └──< schedule_entry_slots >── time_slots
```

## `programs`

Reference data for academic programs.

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Primary key |
| `name` | TEXT | Required; normalized uniqueness within level |
| `level` | TEXT | `UG` or `PG` |
| `is_active` | BOOLEAN | Default `TRUE` |

Examples: MCA, MBA, MTech.

Program level is part of the teacher's foreign-key relationship, so a PG teacher cannot reference a UG program record (and vice versa).

## `teachers`

Stores faculty identity and academic context.

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Primary key |
| `name` | TEXT | Required; cannot be blank |
| `acronym` | TEXT | Required; normalized unique |
| `level` | TEXT | `UG` or `PG` |
| `program_id` | UUID | FK to matching program + level |
| `semester` | SMALLINT | Positive |
| `department` | TEXT | Required; prototype default provided |
| `is_active` | BOOLEAN | Default `TRUE` |
| `created_at` | TIMESTAMPTZ | Default now |
| `updated_at` | TIMESTAMPTZ | Default now |

Acronym uniqueness is case-insensitive and whitespace-normalized using a unique expression index. This means `KR`, `kr`, and ` KR ` cannot represent different teachers.

## `time_slots`

Fixed department scheduling configuration.

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Primary key |
| `code` | TEXT | Unique, e.g. `S1`–`S9` |
| `start_time` | TIME | Required |
| `end_time` | TIME | Must be after start |
| `sequence` | SMALLINT | Unique ordering |
| `is_active` | BOOLEAN | Default `TRUE` |

The prototype seeds the department's nine teaching slots separately in `database/timetable_slots.sql`.

## `timetables`

Represents a version of one teacher's schedule.

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Primary key |
| `teacher_id` | UUID | FK to teacher; cascade on teacher deletion |
| `academic_year` | TEXT | Required; cannot be blank |
| `effective_from` | DATE | Optional |
| `effective_to` | DATE | Optional; cannot precede `effective_from` |
| `status` | TEXT | `DRAFT` or `CONFIRMED` |
| `source` | TEXT | `MANUAL` or `IMPORT` |
| `last_verified_at` | TIMESTAMPTZ | Optional |
| `created_at` | TIMESTAMPTZ | Default now |
| `updated_at` | TIMESTAMPTZ | Default now |

MVP rule: a teacher can have only one `CONFIRMED` timetable for a given academic year. Multiple drafts may exist during editing/import workflows.

## `schedule_entries`

Represents one logical occupied activity on one day.

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Primary key |
| `timetable_id` | UUID | FK to timetable; cascade on deletion |
| `day_of_week` | SMALLINT | `1`–`7` (`1=Monday`) |
| `entry_type` | TEXT | `CLASS`, `LAB`, or `OTHER` |
| `subject_or_activity` | TEXT | Required; cannot be blank |
| `section` | TEXT | Optional |
| `room` | TEXT | Optional |
| `notes` | TEXT | Optional |

A schedule entry does not directly store a start/end time. It references one or more configured time slots through `schedule_entry_slots`.

## `schedule_entry_slots`

Join table between logical activities and the slots they occupy.

| Field | Type | Rules |
|---|---|---|
| `schedule_entry_id` | UUID | FK to schedule entry |
| `time_slot_id` | UUID | FK to configured time slot |

Primary key: (`schedule_entry_id`, `time_slot_id`).

This is what lets one logical lab occupy two consecutive periods. For example:

```text
Machine Learning Lab
   ├── S6
   └── S7
```

## Why `FREE` is not stored

`FREE` is derived state, not persistent timetable data.

```text
configured working slots
        -
occupied schedule-entry slots
        =
free slots
```

Break and lunch are system-defined and are displayed separately. They are not counted as teacher free periods.

## Database-enforced rules

The schema enforces:

- nonblank teacher/program/activity fields
- UG/PG values
- program/teacher level consistency
- normalized case-insensitive acronym uniqueness
- valid time-slot times
- valid day range
- valid timetable status/source
- valid effective date order
- at most one confirmed timetable per teacher and academic year
- no overlapping activities in a confirmed timetable for the same day and slot

Some higher-level rules remain service-layer responsibilities, especially lab consecutiveness, working-day configuration, import confidence, and complete payload validation.

## Import safety

Imported timetable data must first exist as a `DRAFT`. Only after teacher review, validation, and confirmation can it become the timetable shown to the Director.
