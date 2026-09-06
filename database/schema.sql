-- Initial schema contract for the teacher availability prototype.
-- Implementation detail may evolve through migrations.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS programs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('UG', 'PG')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS teachers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    acronym TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('UG', 'PG')),
    program_id UUID NOT NULL REFERENCES programs(id),
    semester SMALLINT NOT NULL CHECK (semester > 0),
    department TEXT NOT NULL DEFAULT 'Prototype Department',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (acronym)
);

CREATE TABLE IF NOT EXISTS time_slots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    sequence SMALLINT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CHECK (start_time < end_time)
);

CREATE TABLE IF NOT EXISTS timetables (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    academic_year TEXT NOT NULL,
    effective_from DATE,
    effective_to DATE,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'CONFIRMED')),
    source TEXT NOT NULL CHECK (source IN ('MANUAL', 'IMPORT')),
    last_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_from <= effective_to)
);

CREATE TABLE IF NOT EXISTS schedule_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timetable_id UUID NOT NULL REFERENCES timetables(id) ON DELETE CASCADE,
    day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    entry_type TEXT NOT NULL CHECK (entry_type IN ('CLASS', 'LAB', 'OTHER')),
    subject_or_activity TEXT NOT NULL,
    section TEXT,
    room TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS schedule_entry_slots (
    schedule_entry_id UUID NOT NULL REFERENCES schedule_entries(id) ON DELETE CASCADE,
    time_slot_id UUID NOT NULL REFERENCES time_slots(id),
    PRIMARY KEY (schedule_entry_id, time_slot_id)
);

CREATE INDEX IF NOT EXISTS idx_teachers_name ON teachers(name);
CREATE INDEX IF NOT EXISTS idx_teachers_acronym ON teachers(acronym);
CREATE INDEX IF NOT EXISTS idx_timetable_teacher ON timetables(teacher_id);
CREATE INDEX IF NOT EXISTS idx_schedule_entries_timetable_day ON schedule_entries(timetable_id, day_of_week);
