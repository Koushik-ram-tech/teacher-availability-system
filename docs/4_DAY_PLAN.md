# Four-Day Execution Plan

## Day 1 — Foundation + first vertical slices

Freeze architecture, schema, timetable rules, API contracts, frontend routes, and validation rules. Build teacher CRUD and basic timetable persistence. Build director search and schedule retrieval. Both developers work full-stack and review each other's work.

**End-of-day gate:** teacher can save a timetable to PostgreSQL and director can search/open that teacher and retrieve the saved schedule.

## Day 2 — Timetable engine + availability

Implement fixed slot configuration, class/lab modeling, collision validation, timetable update flow, derived free-slot calculation, current daily status, and director daily/weekly views. Add integration tests for labs, breaks, occupied slots, and free slots.

**End-of-day gate:** the application correctly reports occupied, break, and free periods for representative schedules.

## Day 3 — Import pipeline

Implement Excel, DOCX, and PDF extraction. Normalize all parser output to one internal structure. Add teacher-name/acronym matching, confidence/warning handling, editable import preview, validation, and confirmation before persistence.

**End-of-day gate:** a representative file can be uploaded, parsed, corrected, validated, and saved without bypassing review.

## Day 4 — QA + deployment + demo hardening

Freeze feature scope. Test the complete system with realistic department data and intentionally malformed inputs. Fix defects, improve UX, configure deployment, seed demo data, and prepare the Director demonstration.

**Release gate:** core teacher → database → director workflow works end-to-end; availability calculations match independently verified expected results; parser failures are visible rather than silently guessed.

## Team operating model

Both developers are full-stack. Work by vertical feature rather than permanent frontend/backend ownership. Each feature may touch database, API, UI, and tests. Use feature branches, PR review, and `main` as the stable branch.

## P0 features

1. Teacher profile
2. Manual timetable editor
3. Database persistence
4. Director name/acronym search
5. Daily schedule
6. Free-time calculation
7. Weekly schedule
8. Timetable validation
9. Excel import
10. Editable import review

PDF/DOCX import follows immediately after the P0 core workflow and must not compromise the core system.
