-- Final MVP schema contract for the Teacher Availability System.
-- Run this file on a fresh PostgreSQL/Supabase database.
--
-- Design principles:
-- 1. Department time slots are configuration, not teacher-entered times.
-- 2. FREE is derived from working slots minus occupied slots.
-- 3. A lab is one logical schedule entry linked to multiple consecutive slots.
-- 4. Imported schedules remain drafts until reviewed and confirmed.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ------------------------------------------------------------
-- Academic programs
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS programs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('UG', 'PG')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT programs_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT programs_id_level_unique UNIQUE (id, level)
);

-- Case-insensitive program uniqueness within UG/PG.
CREATE UNIQUE INDEX IF NOT EXISTS uq_programs_name_level_normalized
    ON programs (lower(btrim(name)), level);

-- ------------------------------------------------------------
-- Teachers
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teachers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    acronym TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('UG', 'PG')),
    program_id UUID NOT NULL,
    semester SMALLINT NOT NULL CHECK (semester > 0),
    department TEXT NOT NULL DEFAULT 'Prototype Department',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT teachers_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT teachers_acronym_not_blank CHECK (btrim(acronym) <> ''),
    CONSTRAINT teachers_department_not_blank CHECK (btrim(department) <> ''),
    CONSTRAINT teachers_program_level_fk
        FOREIGN KEY (program_id, level)
        REFERENCES programs (id, level)
);

-- Department-aware teacher identity: (normalized_acronym, normalized_department) must be unique.
-- Same acronym is allowed in different departments, but must be unique within a department.
CREATE UNIQUE INDEX IF NOT EXISTS uq_teachers_acronym_department_normalized
    ON teachers (lower(btrim(acronym)), lower(btrim(department)));

-- Efficient department-based lookups.
CREATE INDEX IF NOT EXISTS idx_teachers_department_normalized
    ON teachers (lower(btrim(department)));

-- Efficient case-insensitive name search.
CREATE INDEX IF NOT EXISTS idx_teachers_name_normalized
    ON teachers (lower(btrim(name)));

-- ------------------------------------------------------------
-- Fixed department time slots
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS time_slots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    sequence SMALLINT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT time_slots_code_not_blank CHECK (btrim(code) <> ''),
    CONSTRAINT time_slots_valid_time CHECK (start_time < end_time)
);

-- ------------------------------------------------------------
-- Timetable versions
-- ------------------------------------------------------------
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
    CONSTRAINT timetables_academic_year_not_blank CHECK (btrim(academic_year) <> ''),
    CONSTRAINT timetables_valid_effective_dates
        CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_from <= effective_to)
);

-- In the MVP, one confirmed timetable per teacher per academic year.
-- Multiple draft timetables are allowed so import/edit workflows remain safe.
CREATE UNIQUE INDEX IF NOT EXISTS uq_confirmed_timetable_teacher_year
    ON timetables (teacher_id, academic_year)
    WHERE status = 'CONFIRMED';

-- ------------------------------------------------------------
-- Logical schedule activities
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schedule_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timetable_id UUID NOT NULL REFERENCES timetables(id) ON DELETE CASCADE,
    day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    entry_type TEXT NOT NULL CHECK (entry_type IN ('CLASS', 'LAB', 'OTHER')),
    subject_or_activity TEXT NOT NULL,
    section TEXT,
    room TEXT,
    notes TEXT,
    CONSTRAINT schedule_entries_activity_not_blank CHECK (btrim(subject_or_activity) <> '')
);

-- ------------------------------------------------------------
-- Entry-to-slot mapping
-- ------------------------------------------------------------
-- An entry may occupy multiple slots, which is required for labs.
CREATE TABLE IF NOT EXISTS schedule_entry_slots (
    schedule_entry_id UUID NOT NULL REFERENCES schedule_entries(id) ON DELETE CASCADE,
    time_slot_id UUID NOT NULL REFERENCES time_slots(id),
    PRIMARY KEY (schedule_entry_id, time_slot_id)
);

-- ------------------------------------------------------------
-- Performance indexes
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_timetables_teacher
    ON timetables (teacher_id);

CREATE INDEX IF NOT EXISTS idx_schedule_entries_timetable_day
    ON schedule_entries (timetable_id, day_of_week);

CREATE INDEX IF NOT EXISTS idx_schedule_entry_slots_time_slot
    ON schedule_entry_slots (time_slot_id);

-- ------------------------------------------------------------
-- Confirmed timetable occupancy validation
-- ------------------------------------------------------------
-- Because day_of_week lives on schedule_entries and slot IDs live in the
-- join table, a simple UNIQUE constraint cannot express:
--   one timetable + one day + one slot = one occupied activity.
-- We enforce that rule with a database trigger for confirmed timetables.

CREATE OR REPLACE FUNCTION validate_timetable_slot_occupancy(p_timetable_id UUID)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM schedule_entries se
        JOIN schedule_entry_slots ses
          ON ses.schedule_entry_id = se.id
        JOIN timetables tt
          ON tt.id = se.timetable_id
        WHERE se.timetable_id = p_timetable_id
          AND tt.status = 'CONFIRMED'
        GROUP BY se.day_of_week, ses.time_slot_id
        HAVING COUNT(DISTINCT se.id) > 1
    ) THEN
        RAISE EXCEPTION 'Confirmed timetable contains overlapping activities for the same day and time slot'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION trg_validate_confirmed_schedule()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    timetable_id_to_check UUID;
BEGIN
    IF TG_TABLE_NAME = 'schedule_entries' THEN
        timetable_id_to_check := NEW.timetable_id;
    ELSE
        SELECT se.timetable_id
        INTO timetable_id_to_check
        FROM schedule_entries se
        WHERE se.id = NEW.schedule_entry_id;
    END IF;

    IF timetable_id_to_check IS NOT NULL THEN
        PERFORM validate_timetable_slot_occupancy(timetable_id_to_check);
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_schedule_entries_validate_occupancy
    ON schedule_entries;

CREATE CONSTRAINT TRIGGER trg_schedule_entries_validate_occupancy
AFTER INSERT OR UPDATE OF timetable_id, day_of_week
ON schedule_entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION trg_validate_confirmed_schedule();

DROP TRIGGER IF EXISTS trg_schedule_entry_slots_validate_occupancy
    ON schedule_entry_slots;

CREATE CONSTRAINT TRIGGER trg_schedule_entry_slots_validate_occupancy
AFTER INSERT OR UPDATE OF schedule_entry_id, time_slot_id
ON schedule_entry_slots
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION trg_validate_confirmed_schedule();

CREATE OR REPLACE FUNCTION trg_validate_timetable_confirmation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'CONFIRMED' THEN
        PERFORM validate_timetable_slot_occupancy(NEW.id);
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_timetable_validate_confirmation
    ON timetables;

CREATE CONSTRAINT TRIGGER trg_timetable_validate_confirmation
AFTER INSERT OR UPDATE OF status
ON timetables
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION trg_validate_timetable_confirmation();
