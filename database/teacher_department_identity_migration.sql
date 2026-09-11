-- Teacher Department-Aware Identity Migration
-- This migration changes teacher uniqueness from globally unique normalized acronym
-- to department-scoped uniqueness: (normalized_acronym, department)

-- ------------------------------------------------------------
-- STEP 1: Check current constraint
-- ------------------------------------------------------------
-- Current: uq_teachers_acronym_normalized on lower(btrim(acronym))
-- New: uq_teachers_acronym_department_normalized on (lower(btrim(acronym)), lower(btrim(department)))

-- ------------------------------------------------------------
-- STEP 2: Drop old uniqueness constraint
-- ------------------------------------------------------------
DROP INDEX IF EXISTS uq_teachers_acronym_normalized;

-- ------------------------------------------------------------
-- STEP 3: Add new department-aware uniqueness constraint
-- ------------------------------------------------------------
-- Teachers with the same acronym can exist in different departments
-- but acronym must be unique within a department (case-insensitive)
CREATE UNIQUE INDEX uq_teachers_acronym_department_normalized
    ON teachers (lower(btrim(acronym)), lower(btrim(department)));

-- ------------------------------------------------------------
-- STEP 4: Add index for efficient department-based lookups
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_teachers_department_normalized
    ON teachers (lower(btrim(department)));

-- ------------------------------------------------------------
-- MIGRATION NOTES
-- ------------------------------------------------------------
-- 1. This migration is safe to apply to existing data:
--    - If no duplicate acronyms exist across departments, it succeeds immediately
--    - If duplicates exist, the CREATE UNIQUE INDEX will fail with a clear error
--    - No data is lost or modified
--
-- 2. To check for conflicts before migration:
--    SELECT lower(btrim(acronym)), lower(btrim(department)), COUNT(*)
--    FROM teachers
--    GROUP BY lower(btrim(acronym)), lower(btrim(department))
--    HAVING COUNT(*) > 1;
--
-- 3. If conflicts are found (same acronym + same department):
--    - These are true duplicates that violate the new constraint
--    - Manually resolve by merging or updating one teacher's acronym/department
--    - Then retry the migration
--
-- 4. Backward compatibility:
--    - Existing single-department data works unchanged
--    - Teacher identity now includes department context
--    - APIs and import logic updated to use (acronym, department) for lookups

