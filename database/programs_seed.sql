-- Prototype academic program seed data.
-- Program names are unique case-insensitively within each UG/PG level.
-- Keep this file idempotent so it can be safely re-run.

INSERT INTO programs (name, level)
VALUES
    ('MCA', 'PG'),
    ('MBA', 'PG'),
    ('MTech', 'PG')
ON CONFLICT DO NOTHING;
