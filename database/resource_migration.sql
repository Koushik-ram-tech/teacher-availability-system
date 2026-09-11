-- Resource Foundation Migration
-- This migration adds the resource model without breaking existing functionality.
-- The room TEXT column is preserved for backward compatibility.

-- ------------------------------------------------------------
-- Resources table
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS resources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('LAB', 'CLASSROOM', 'SEMINAR_HALL', 'AUDITORIUM', 'OTHER')),
    department TEXT,  -- NULL means shared resource
    capacity SMALLINT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT resources_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT resources_normalized_name_not_blank CHECK (btrim(normalized_name) <> ''),
    CONSTRAINT resources_capacity_positive CHECK (capacity IS NULL OR capacity > 0)
);

-- Unique constraint: normalized name + department (or NULL for shared)
-- This prevents duplicate resources within the same scope
CREATE UNIQUE INDEX IF NOT EXISTS uq_resources_normalized_name_department
    ON resources (normalized_name, department)
    WHERE department IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_resources_normalized_name_shared
    ON resources (normalized_name)
    WHERE department IS NULL;

-- Index for searching resources by department
CREATE INDEX IF NOT EXISTS idx_resources_department
    ON resources (department)
    WHERE department IS NOT NULL;

-- Index for active resources
CREATE INDEX IF NOT EXISTS idx_resources_active
    ON resources (is_active)
    WHERE is_active = TRUE;

-- ------------------------------------------------------------
-- Resource aliases table
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS resource_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resource_id UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT resource_aliases_alias_not_blank CHECK (btrim(alias) <> ''),
    CONSTRAINT resource_aliases_normalized_alias_not_blank CHECK (btrim(normalized_alias) <> '')
);

-- Unique constraint: one normalized alias can only point to one resource
-- This ensures deterministic resolution
CREATE UNIQUE INDEX IF NOT EXISTS uq_resource_aliases_normalized
    ON resource_aliases (normalized_alias);

-- Index for finding aliases by resource
CREATE INDEX IF NOT EXISTS idx_resource_aliases_resource_id
    ON resource_aliases (resource_id);

-- ------------------------------------------------------------
-- Add resource_id to schedule_entries
-- ------------------------------------------------------------
-- This is a non-breaking migration. The room TEXT column remains.
-- resource_id is nullable to allow gradual migration.
ALTER TABLE schedule_entries
ADD COLUMN IF NOT EXISTS resource_id UUID REFERENCES resources(id);

-- Index for resource usage queries
CREATE INDEX IF NOT EXISTS idx_schedule_entries_resource
    ON schedule_entries (resource_id)
    WHERE resource_id IS NOT NULL;

-- Comment to document the migration strategy
COMMENT ON COLUMN schedule_entries.resource_id IS 
    'Normalized resource reference. NULL means unresolved or legacy data. room TEXT column preserved for backward compatibility.';

COMMENT ON COLUMN schedule_entries.room IS 
    'Legacy free-text room field. Preserved for backward compatibility during resource migration. resource_id takes precedence when not NULL.';
