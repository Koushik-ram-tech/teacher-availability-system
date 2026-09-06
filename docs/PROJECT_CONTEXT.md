# Teacher Availability System — Shared Project Context

## 1. Project purpose

This is a 4-day department-level prototype for teachers and the college director. Teachers maintain their weekly schedules; the director searches for a teacher and immediately sees that teacher's daily/weekly schedule and derived free periods.

The prototype is intentionally scoped to one department, but the data model should not prevent later college-wide expansion.

## 2. Primary users

### Teacher
- Enter name and institutional acronym.
- Select UG or PG.
- Select the academic program/branch (for example MCA, MBA, MTech).
- Select the semester being taught.
- Enter/edit a weekly timetable using predefined department time slots.
- Upload a timetable file in Excel, DOCX, or PDF format.
- Review the parsed draft, correct mistakes, validate it, and save it.

### Director
- Search teachers by full/partial name or acronym, case-insensitively.
- Open a teacher profile.
- See today's schedule, current status, and free periods.
- Switch days and view the whole week.

## 3. Core business idea

The application is a timetable/availability system, not a generic calendar. The department has fixed time slots. Teachers are assigned to different slots. `FREE` is not stored; it is calculated from configured working slots minus occupied slots.

## 4. Department timetable rules

Default slots currently configured:
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

Labs normally occupy two consecutive 55-minute slots, giving 1 hour 50 minutes. The system should model labs as one schedule entry linked to multiple slots.

Monday–Friday are the initial default working days. Saturday should remain configurable rather than hard-coded as always active.

Do not let teachers type arbitrary times for normal timetable entries. They select from configured slots.

## 5. Architecture

Recommended stack for the prototype:
- Frontend: Next.js + TypeScript
- Backend: FastAPI + Python
- Database: PostgreSQL (Supabase is a practical hosted option)
- File parsing: Python libraries appropriate to format: openpyxl for Excel, python-docx for DOCX, PyMuPDF for PDF
- API style: REST

The parsers must normalize all supported input formats into one internal timetable representation. The parser is an input method, not the source of truth.

## 6. Data model

### programs
Reference data for academic programs.
Fields:
- id UUID primary key
- name text
- level UG/PG
- is_active boolean

Examples: MCA, MBA, MTech.

### teachers
Stores faculty identity and academic context.
Fields:
- id UUID primary key
- name text required
- acronym text required and unique for the prototype
- level UG/PG
- program_id foreign key to programs
- semester positive integer
- department text
- is_active boolean
- created_at/updated_at timestamps

### time_slots
Fixed department timetable configuration.
Fields:
- id UUID primary key
- code S1–S9
- start_time
- end_time
- sequence
- is_active

### timetables
A teacher's schedule version.
Fields:
- id UUID primary key
- teacher_id foreign key
- academic_year
- effective_from/effective_to optional dates
- status DRAFT or CONFIRMED
- source MANUAL or IMPORT
- last_verified_at
- created_at/updated_at

### schedule_entries
One occupied activity.
Fields:
- id UUID primary key
- timetable_id foreign key
- day_of_week 1–7
- entry_type CLASS, LAB, or OTHER
- subject_or_activity
- section optional
- room optional
- notes optional

### schedule_entry_slots
Join table between an entry and one or more time slots. Required because a lab may occupy two consecutive slots.
Primary key: (schedule_entry_id, time_slot_id).

## 7. Why FREE is derived

Never save a `FREE` row in the database as the source of truth. Availability is calculated as:

`configured working slots - teacher occupied slots = free slots`

Breaks and lunch are fixed schedule configuration, not teacher-created free periods.

## 8. Data flow

Manual path:
Teacher → timetable form → validation → draft/confirm → PostgreSQL

Import path:
File upload → file-type parser → normalized timetable draft → identity matching → validation → editable preview → teacher correction → confirmation → PostgreSQL

Director path:
Search teacher → retrieve confirmed timetable → calculate occupied/free state → daily or weekly presentation.

Imported data must never be written directly into confirmed timetable data before human review/confirmation.

## 9. Important validation rules

At minimum:
- valid day
- valid configured slot
- no duplicate occupancy for the same teacher/day/slot in a confirmed timetable
- lab entries must use valid consecutive slots according to department rules
- required teacher identity fields
- acronym normalization and uniqueness checks
- imported identity must match teacher name or acronym, otherwise show a warning/manual confirmation path
- no saving an invalid or unresolved timetable
- a teacher should not have overlapping active/confirmed timetable versions for the same effective period

Some of these constraints belong in the database; others need service-layer validation because PostgreSQL alone cannot express the business rule conveniently in the current normalized model.

## 10. Director behavior

Search should be:
- case-insensitive
- partial-match friendly
- name-aware
- acronym-aware

Director's daily view should clearly show:
- teacher identity
- current status where a current-time comparison is applicable
- each timetable slot
- break/lunch
- free periods
- full-week option

## 11. Prototype non-goals

Do not expand the 4-day prototype with college-wide administration, mobile apps, notifications, leave management, room allocation, substitution workflows, attendance, SSO, WhatsApp integrations, or chatbot features. Those belong to later phases after Director approval.

## 12. Team working model

There are two developers. Do not permanently split into frontend/backend. Work in vertical slices so both people touch database + API + UI + tests.

Recommended workflow:
1. Create feature branch.
2. Implement one bounded feature end-to-end.
3. Add tests.
4. Commit.
5. Open PR.
6. Teammate reviews.
7. Merge to main.

`main` must remain the stable integration branch.

## 13. 4-day delivery target

Day 1: database/API/frontend foundation + teacher and director core vertical slices.
Day 2: timetable validation, labs, availability engine, daily/weekly director views.
Day 3: Excel/DOCX/PDF import, normalization, identity matching, editable review flow.
Day 4: aggressive end-to-end testing, edge cases, bug fixes, deployment, demo data, final demo.

## 14. Definition of success

The end-to-end demo must work:
Teacher creates or uploads timetable → system parses/normalizes → teacher reviews/edits → timetable is confirmed in PostgreSQL → Director searches by name/acronym → Director sees today's timetable and accurately derived free periods → Director can view the full week.

## 15. Development principle

Prefer deterministic rules over hidden assumptions. Do not invent timetable slots, days, branch mappings, or parsing interpretations silently. When input is ambiguous, preserve it as a draft warning for human correction.
