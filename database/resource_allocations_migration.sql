-- Migration script to add resource_allocations and related join tables for external-only groups

CREATE TABLE IF NOT EXISTS resource_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    academic_year VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'DRAFT',
    source_import_id VARCHAR,
    day_of_week SMALLINT NOT NULL,
    subject_or_activity VARCHAR NOT NULL,
    section VARCHAR,
    notes VARCHAR,
    source_cell_text VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT resource_allocations_subject_not_blank CHECK (btrim(subject_or_activity) <> '')
);

CREATE TABLE IF NOT EXISTS resource_allocation_slots (
    allocation_id UUID NOT NULL REFERENCES resource_allocations(id) ON DELETE CASCADE,
    time_slot_id UUID NOT NULL REFERENCES time_slots(id),
    PRIMARY KEY (allocation_id, time_slot_id)
);

CREATE TABLE IF NOT EXISTS resource_allocation_resources (
    allocation_id UUID NOT NULL REFERENCES resource_allocations(id) ON DELETE CASCADE,
    resource_id UUID NOT NULL REFERENCES resources(id),
    PRIMARY KEY (allocation_id, resource_id)
);

-- Performance indexes
CREATE INDEX idx_resource_allocations_academic_year ON resource_allocations(academic_year);
CREATE INDEX idx_resource_allocations_status ON resource_allocations(status);
CREATE INDEX idx_res_alloc_res_resource_id ON resource_allocation_resources(resource_id);
