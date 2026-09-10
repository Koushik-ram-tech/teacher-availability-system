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

### GET (read a single day's schedule)

```
GET /api/v1/teachers/{teacher_id}/timetable
    ?day=<day_name>
    &academic_year=<year>
    &status=<DRAFT|CONFIRMED>
```

All three query parameters are **required**.

#### Exact matching

The server resolves the timetable by the tuple `(teacher_id, academic_year, status)` with no fallback, no inference, and no cross-year lookup.

- `academic_year` — must be supplied by the caller verbatim (e.g. `2025-2026`). The server never infers a "current year".
- `status` — must be exactly `DRAFT` or `CONFIRMED`. No automatic fallback from CONFIRMED to DRAFT or vice versa.

#### 404 semantics

A **404** response means the exact requested timetable does not exist.

Only a 404 may be interpreted by the frontend as "no timetable exists yet for this combination".

All other errors (`400`, `409`, `422`, `500`, network errors, timeouts, CORS errors) must be treated as real errors by the frontend and must not be silently treated as "empty schedule".

#### Response shape

```json
{
  "teacher_id": "uuid",
  "academic_year": "2025-2026",
  "day": "monday",
  "timetable_status": "DRAFT",
  "periods": [
    {
      "kind": "SLOT",
      "code": "S1",
      "start_time": "08:00:00",
      "end_time": "08:55:00",
      "entry": null
    },
    {
      "kind": "BREAK",
      "label": "Morning break",
      "start_time": "10:45:00",
      "end_time": "11:15:00",
      "entry": null
    }
  ]
}
```

#### Purpose of DRAFT vs CONFIRMED

- **DRAFT** — teacher editor reads and writes only DRAFT timetables. The teacher's daily editing never touches a CONFIRMED timetable.
- **CONFIRMED** — Director view reads only CONFIRMED timetables. No fallback to DRAFT.

### POST (create a new draft)

```
POST /api/v1/teachers/{teacher_id}/timetable
```

Creates a new **DRAFT** timetable for the exact `(teacher_id, academic_year)` supplied in the body.
Returns `409` if a DRAFT already exists for that combination (use PUT to update).
Never creates or modifies a CONFIRMED timetable.

### PUT (replace an existing draft)

```
PUT /api/v1/teachers/{teacher_id}/timetable
```

Atomically replaces all entries in the existing **DRAFT** timetable identified by `(teacher_id, academic_year)`.
Returns `404` if no DRAFT exists for that combination (use POST to create).
Validation runs before any deletion; a constraint failure leaves the previous draft intact.
Never touches a CONFIRMED timetable.

### Normalized schedule payload (POST and PUT body)

```json
{
  "academic_year": "2025-2026",
  "days": {
    "monday": [
      {
        "slot_ids": ["S1"],
        "entry_type": "CLASS",
        "subject_or_activity": "Database Management Systems",
        "section": "MCA-A",
        "room": "204",
        "notes": null
      },
      {
        "slot_ids": ["S6", "S7"],
        "entry_type": "LAB",
        "subject_or_activity": "DBMS Lab",
        "section": null,
        "room": "Lab 3",
        "notes": null
      }
    ]
  }
}
```

`slot_ids` contains canonical slot codes (`S1`–`S9`). A lab spanning two slots uses a single entry with multiple codes in `slot_ids`.

## Error contract

Use consistent HTTP statuses and machine-readable error responses:

- `400` invalid request / validation input
- `404` teacher or timetable not found (exact match)
- `409` draft already exists (POST) / schedule conflict
- `422` semantically invalid data (Pydantic validation failure)
- `500` unexpected server error
