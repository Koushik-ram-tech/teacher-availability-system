-- Keep the deferred timetable occupancy trigger consistent with canonical
-- validation: only identical imported logical-source/group occurrences may
-- share a teacher/day/slot across sections.

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
           AND (
               BOOL_OR(se.source_cell_text IS NULL OR se.source_cell_text = '')
               OR COUNT(DISTINCT (
                   se.source_cell_text,
                   se.group_index,
                   se.subject_or_activity,
                   se.entry_type
               )) > 1
           )
    ) THEN
        RAISE EXCEPTION 'Confirmed timetable contains overlapping activities for the same day and time slot'
            USING ERRCODE = '23514';
    END IF;
END;
$$;