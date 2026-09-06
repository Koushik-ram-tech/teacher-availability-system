# Teacher Availability System

Department-level prototype for managing faculty timetables and allowing the Director to quickly view teacher schedules and availability.

## 4-Day MVP

Core workflow:

Teacher → enter/upload timetable → editable review → validation → PostgreSQL → Director search → daily availability → weekly schedule

## Planned Stack

- Frontend: Next.js + TypeScript
- Backend: FastAPI + Python
- Database: PostgreSQL / Supabase
- Timetable parsing: Excel, DOCX, PDF

## Repository Structure

```text
frontend/       Next.js application
backend/        FastAPI application
database/       schema, seeds, migrations
docs/           architecture and project contracts
sample_files/  parser test inputs
```

## Timetable Rules

The prototype uses fixed department time slots. Teachers select/edit schedule entries against predefined slots rather than typing arbitrary class times.

Default working days are Monday-Friday. Saturday is configurable.

## Development Rule

`main` is the stable branch. Feature work is developed on branches and merged only after review and tests pass.
