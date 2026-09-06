# Data Model

## Teacher

Stores faculty identity and academic context.

Suggested fields:

- `id` UUID primary key
- `name` text, required
- `acronym` text, required, unique within the prototype
- `level` enum: `UG` or `PG`
- `program` text, required
- `semester` small integer, required
- `department` text, required for future college-wide expansion
- `is_active` boolean, default true
- `created_at` timestamp
- `updated_at` timestamp

## Program

Reference data for academic programs.

- `id`
- `name`
- `level`
- `is_active`

Examples: MCA, MBA, MTech.

## Time Slot

Fixed department scheduling configuration.

- `id`
- `code` (`S1` ... `S9`)
- `start_time`
- `end_time`
- `sequence`
- `is_active`

## Timetable

Represents a teacher's current schedule version for the department prototype.

- `id`
- `teacher_id` foreign key
- `academic_year` text
- `effective_from` date, nullable
- `effective_to` date, nullable
- `status` enum: `DRAFT`, `CONFIRMED`
- `source` enum: `MANUAL`, `IMPORT`
- `last_verified_at` timestamp, nullable
- `created_at`
- `updated_at`

## Schedule Entry

Represents an occupied teaching/institutional activity.

- `id`
- `timetable_id` foreign key
- `day_of_week`
- `entry_type`: `CLASS`, `LAB`, `OTHER`
- `subject_or_activity` text
- `section` text, nullable
- `room` text, nullable
- `notes` text, nullable

A schedule entry is linked to one or more slot IDs.

## Schedule Entry Slots

Join table needed because a lab can span multiple consecutive slots.

- `schedule_entry_id`
- `time_slot_id`

Primary key: (`schedule_entry_id`, `time_slot_id`).

## Important constraints

- One teacher should not have two confirmed entries occupying the same day + slot.
- A teacher should have at most one active timetable for the same effective period in the prototype.
- Acronym should be normalized for matching and uniqueness checks.
- Imported timetable drafts remain isolated from confirmed timetable data until explicitly saved.

## Why free is not stored

`FREE` is a derived state. Storing it creates synchronization problems when a class is added or removed. The availability service instead derives free slots from configured working slots minus occupied slots.
