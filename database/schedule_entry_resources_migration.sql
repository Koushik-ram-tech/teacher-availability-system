-- schedule_entry_resources_migration.sql
-- Adds the many-to-many join table between schedule_entries and resources.
-- This is a NON-BREAKING, fully additive migration.
-- schedule_entries.resource_id is retained for backwards compatibility.

CREATE TABLE IF NOT EXISTS schedule_entry_resources (
    schedule_entry_id UUID NOT NULL
        REFERENCES schedule_entries(id) ON DELETE CASCADE,
    resource_id       UUID NOT NULL
        REFERENCES resources(id),
    PRIMARY KEY (schedule_entry_id, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_schedule_entry_resources_entry
    ON schedule_entry_resources (schedule_entry_id);

CREATE INDEX IF NOT EXISTS idx_schedule_entry_resources_resource
    ON schedule_entry_resources (resource_id);

-- Backfill: copy existing singular resource_id links into the join table.
INSERT INTO schedule_entry_resources (schedule_entry_id, resource_id)
SELECT id, resource_id
FROM schedule_entries
WHERE resource_id IS NOT NULL
ON CONFLICT DO NOTHING;

COMMENT ON TABLE schedule_entry_resources IS
    'Many-to-many join between schedule_entries and resources. '
    'Authoritative source for resource occupancy queries. '
    'Replaces the singular resource_id FK for multi-resource cases.';
