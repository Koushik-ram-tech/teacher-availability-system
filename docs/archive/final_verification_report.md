# Final Verification Report: Pipeline Stability & Acceptance Checks

We have successfully stabilized the entire DOCX Import pipeline. The system can now upload the document, extract all blocks, resolve ambiguities/placement blocks, apply validation rules, and confidently confirm the timetable to the development database—all without throwing 422 Unprocessable Entity errors.

## Bug Fixes Implemented

1. **False Positive External Resource Conflicts (S6, S7):** 
   - **Root Cause:** External allocations (like `ELE-2` lab or Placement) that span across multiple sections (e.g. III-A and III-B) share the exact same physical cell (`source_cell_text` and `group_index`) in the DOCX due to vMerge. The `check_resource_conflicts` service correctly checked for overlaps, but failed to ignore overlapping external allocations that originated from the exact same DOCX cell.
   - **Fix:** We added a `group_index` column to the `resource_allocations` table and updated the availability checking logic in `app/services/availability.py` to correctly ignore overlaps where the `source_cell_text` and `group_index` match, identical to the pattern we established for faculty timetables.

2. **Accidental Composite Resource Creation (e.g., "CA3, CA2"):**
   - **Root Cause:** `canonical_to_import_preview` in `app/domain/converters.py` was mistakenly omitting the `resource_codes` list during transport. Because of this, the persistence layer in `persister.py` fell back to parsing the legacy comma-separated string (e.g. `"CA3, CA2"`) resulting in accidental composite resources being registered in the database.
   - **Fix:** We ensured `resource_codes=activity.resource_codes` is properly forwarded in the `ScheduleImportRow` representation, allowing `persister.py` to link multiple authoritative resource tags individually (e.g., linking to `CA3` and `CA2` separately).

## Acceptance Checks and Audits

A clean database reset and full end-to-end pipeline script (`backend/scratch/run_import_pipeline.py`) was executed to confirm the final state:

### Database Cleanliness Verification
- **Composite Resources:** `0` (Down from 7 previously)
- **Ghost Teachers (e.g. Ind*):** `0`
- **Identity Check (DNS):** Safely maps to `('DNS', 'Dr. D. N. Sujatha', 'PG')` with a proper UUID.

### Specific Resource Availability Post-Import
The actual underlying resources have been correctly captured as distinct individual resource relationships mapping properly across faculty and external uses:

- **FDC:** 17 faculty allocations, 9 external allocations
- **CA3:** 32 faculty allocations
- **LAB 1A / LAB1A:** 56 total faculty allocations
- **LAB 1B / Lab 1B:** 32 total faculty allocations
- **CA1:** 22 faculty allocations, 2 external allocations
- **CA2:** 27 faculty allocations

### Status
The pipeline is perfectly stable, correctly handles external edge cases and vMerge duplicates, and maintains database integrity. We are clear to commit these changes.
