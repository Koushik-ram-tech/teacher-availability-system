# Teacher Availability System

Department-level prototype for managing faculty timetables and allowing the Director to quickly view teacher schedules and availability.

## 4-Day MVP

Core workflow:

```text
Teacher → enter/upload timetable → editable review → validation → PostgreSQL
→ Director search → daily availability → weekly schedule
```

## Stack

- Frontend: React + TypeScript + Vite
- Frontend data: TanStack Query + Axios
- Routing: React Router
- Backend: FastAPI + Python
- Database: PostgreSQL / Supabase
- Timetable parsing: Excel, DOCX, PDF

## Repository Structure

```text
frontend/       React + Vite application
backend/        FastAPI application
database/       schema, seeds, migrations
docs/           architecture and project contracts
sample_files/   parser test inputs
```

## Current Day-1 Vertical Slice

The first end-to-end slice is teacher identity persistence and Director search:

```text
React form
   ↓
FastAPI
   ↓
Supabase PostgreSQL
   ↓
Teacher stored/retrieved
   ↓
Director searches by name/acronym
```

Timetable CRUD, availability, import parsing, and review workflows follow the four-day plan in `docs/4_DAY_PLAN.md`.

## Timetable Rules

The prototype uses fixed department time slots. Teachers select/edit schedule entries against predefined slots rather than typing arbitrary class times.

Default working days are Monday-Friday. Saturday is configurable.

## Development Rule

`main` is the stable branch. Feature work is developed on branches and merged only after review and tests pass.
