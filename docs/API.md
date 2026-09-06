# API Contract (MVP)

The API is versioned under `/api/v1`.

## Health

`GET /api/v1/health`

Returns service health and verifies that the FastAPI process can reach PostgreSQL.

## Programs

`GET /api/v1/teachers/programs`

Return active academic programs used by the teacher profile form. The endpoint is currently nested under the teacher router to keep the Day-1 vertical slice small; it may move to `/programs` without changing the database contract later.

## Teachers

`POST /api/v1/teachers`

Create a teacher.

`GET /api/v1/teachers`

List active teachers.

`GET /api/v1/teachers/search?q=<term>`

Case-insensitive partial search by name or acronym.

`GET /api/v1/teachers/{teacher_id}`

Return teacher profile.

`PUT /api/v1/teachers/{teacher_id}`

Update teacher profile.

## Timetable

`POST /api/v1/teachers/{teacher_id}/timetable`

Create or replace a draft timetable from a normalized payload.

`GET /api/v1/teachers/{teacher_id}/timetable?day=<day>`

Return the selected day's schedule including configured slots and breaks.

`GET /api/v1/teachers/{teacher_id}/timetable/week`

Return the complete weekly schedule.

`PUT /api/v1/teachers/{teacher_id}/timetable`

Update a timetable after teacher edits.

`POST /api/v1/teachers/{teacher_id}/timetable/confirm`

Validate and confirm a draft timetable.

## Availability

`GET /api/v1/teachers/{teacher_id}/availability?day=<day>`

Return occupied slots, free slots, and fixed breaks for the day.

The service derives free periods from configured working slots minus occupied schedule-entry slots.

## Import

`POST /api/v1/import/parse`

Accept an Excel, DOCX, or PDF file and return a normalized draft plus warnings. This endpoint does not persist a confirmed timetable.

`POST /api/v1/import/validate`

Validate a normalized import draft against timetable rules and teacher identity.

## Normalized schedule payload

```json
{
  "teacher": {
    "name": "Example Teacher",
    "acronym": "ET"
  },
  "days": {
    "monday": [
      {
        "slot_ids": ["S1"],
        "entry_type": "CLASS",
        "subject_or_activity": "Database Management Systems",
        "section": "MCA-A",
        "room": "204",
        "notes": null
      }
    ]
  }
}
```

## Error contract

Use consistent HTTP statuses and machine-readable error responses:

- `400` invalid request/validation input
- `404` teacher or timetable not found
- `409` schedule conflict or duplicate identity
- `413` unsupported/oversized upload
- `422` semantically invalid normalized data
- `500` unexpected server error

Import warnings are returned as structured objects so the frontend can show exactly what requires correction.
