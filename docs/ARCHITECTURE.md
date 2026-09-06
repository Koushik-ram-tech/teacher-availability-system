# Architecture

## Goal

Provide a reliable department-level faculty timetable and availability system for a four-day prototype.

## Main workflow

```text
Teacher
  ↓
Manual timetable entry OR file upload
  ↓
Normalized schedule draft
  ↓
Validation + teacher review
  ↓
Database
  ↓
Director search
  ↓
Daily schedule / free periods / weekly schedule
```

## Components

### Frontend

Next.js + TypeScript.

Responsibilities:
- Teacher profile and timetable editor
- Upload and import review UI
- Director search and schedule views
- Client-side form validation and API integration

### Backend

FastAPI + Python.

Responsibilities:
- REST API
- Business rules
- Timetable validation
- Availability calculation
- File parsing and normalization
- Teacher/acronym matching

### Database

PostgreSQL (Supabase is acceptable for the prototype).

Responsibilities:
- Teacher/program/semester data
- Timetable metadata
- Fixed time-slot definitions
- Confirmed schedule entries

## Core design rules

1. Time slots are predefined by department configuration.
2. Free time is derived from occupied slots; it is not manually stored as a source of truth.
3. Break and lunch periods are system-defined.
4. A lab may occupy multiple consecutive slots and must be represented as one logical activity linked to its occupied slots.
5. Imported data is never written directly to the confirmed timetable. It first becomes an editable draft.
6. Parser output from Excel, DOCX, and PDF must normalize into the same internal schedule structure.
7. Ambiguous parser results must be surfaced for human correction instead of silently guessed.
8. `main` is the stable integration branch.
