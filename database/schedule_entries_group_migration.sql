-- Add group_index and source_cell_text to schedule_entries
ALTER TABLE schedule_entries
ADD COLUMN IF NOT EXISTS group_index SMALLINT,
ADD COLUMN IF NOT EXISTS source_cell_text TEXT;
