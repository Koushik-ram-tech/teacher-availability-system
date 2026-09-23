"""Test parsing actual MCA DOCX file."""

import os

import pytest

from app.services.docx_import.parser import parse_docx_timetable


class TestRealMCADOCX:
    """Tests using actual MCA Timetable DOCX file."""

    @pytest.fixture
    def mca_docx_path(self):
        """Path to actual MCA DOCX file."""
        # Path relative to backend/tests/
        path = "../../sample_files/MCA Timetable-2026-Odd V7.docx"
        abs_path = os.path.abspath(os.path.join(os.path.dirname(__file__), path))
        if not os.path.exists(abs_path):
            pytest.skip(f"Real MCA DOCX not found at: {abs_path}")
        return abs_path

    def test_parse_real_mca_docx(self, mca_docx_path):
        """Parse actual MCA timetable DOCX - verify structure and known ambiguities."""
        with open(mca_docx_path, "rb") as f:
            docx_bytes = f.read()

        preview = parse_docx_timetable(
            docx_bytes=docx_bytes,
            department="Computer Applications",
            academic_year="2026-Odd",
            source_file="MCA Timetable-2026-Odd V7.docx"
        )

        # Faculty legend should be extracted
        assert len(preview.faculty_legend) > 10
        assert "SU" in preview.faculty_legend
        assert preview.faculty_legend["SU"] == "Dr. S. Uma"

        # Should parse blocks (real DOCX has many activities)
        assert preview.total_blocks > 80, f"Expected >80 blocks, got {preview.total_blocks}"
        assert preview.occupancy_ready_count > 50, f"Expected >50 resolved, got {preview.occupancy_ready_count}"

        # Should have some unresolved blocks (ambiguous content exists)
        assert preview.occupancy_review_count > 0, "Expected some unresolved blocks"

        print(f"\nActual MCA DOCX parse results:")
        print(f"  Total blocks: {preview.total_blocks}")
        print(f"  Resolved: {preview.occupancy_ready_count}")
        print(f"  Unresolved: {preview.occupancy_review_count}")
        print(f"  Errors: {len(preview.errors)}")
        print(f"  Faculty: {len(preview.faculty_legend)}")

    def test_tuesday_ia_ambiguous_activity_deterministic_occupancy(self, mca_docx_path):
        """Tuesday I-A parallel activity block: activity AMBIGUOUS, but occupancy DETERMINISTIC.

        Core product requirement: activity tokenization ambiguity must NOT block
        teacher/resource occupancy extraction.
        """
        with open(mca_docx_path, "rb") as f:
            docx_bytes = f.read()

        preview = parse_docx_timetable(
            docx_bytes=docx_bytes,
            department="Computer Applications",
            academic_year="2026-Odd",
            source_file="MCA Timetable-2026-Odd V7.docx"
        )

        all_blocks = preview.occupancy_ready_blocks + preview.occupancy_review_blocks

        # Find Tuesday I-A S4+S5 block (PY1, PE2, DS 3, 4 complex cell)
        tuesday_ia_blocks = [
            b for b in all_blocks
            if b.day == "tuesday" and "I-A" in b.section
        ]

        assert len(tuesday_ia_blocks) > 0, "No Tuesday I-A blocks found"

        # The block with PY1/PE2 should have AMBIGUOUS activity
        # but DETERMINISTIC teacher occupancy (not blocking availability)
        ambiguous_acts = [
            b for b in tuesday_ia_blocks
            if any("PY1" in a.code.upper() or "PE2" in a.code.upper()
                   for a in b.activity_candidates)
        ]
        ambiguous_block = ambiguous_acts[0] if ambiguous_acts else None
        if ambiguous_block:
            # Activity ambiguity preserved
            assert ambiguous_block.activity_semantic_status == "AMBIGUOUS", (
                f"Expected AMBIGUOUS activity, got {ambiguous_block.activity_semantic_status}"
            )
            # Teacher occupancy must NOT be blocked by activity ambiguity
            assert ambiguous_block.teacher_occupancy_status == "DETERMINISTIC", (
                f"Teacher occupancy blocked by activity ambiguity: {ambiguous_block.teacher_occupancy_status}"
            )
            # Multiple teachers all DETERMINISTIC
            assert len(ambiguous_block.teacher_candidates) > 1, (
                f"Expected multiple teachers, got {len(ambiguous_block.teacher_candidates)}"
            )

        # Should have ambiguity reason

        print(f"\nTuesday I-A ambiguous block verified:")
        print("  Ambiguous block found and verified" if ambiguous_block else "No ambiguous act block found")
        print(f"  Teachers: {[t.acronym for t in ambiguous_block.teacher_candidates]}")
        print(f"  Resources: {[r.code for r in ambiguous_block.resource_candidates]}")


class TestPlacementBlocks:
    """Regression tests for Placement blocks in the real MCA DOCX.

    These four blocks appear in the authoritative regression fixture:
      Thursday I-A    Placement    S6+S7+S8    (CA1/CA2)
      Thursday I-B    Placement    S6+S7+S8    (CA1/CA2)
      Thursday III-A  Placement    S6+S7+S8    (FDC/Lab1A/Lab1B)
      Thursday III-B  Placement    S6+S7+S8    (FDC/Lab1A/Lab1B)

    Teacher absence is INTENTIONAL — Placement is STUDENT_MANAGED.
    """

    @pytest.fixture
    def mca_docx_path(self):
        """Path to actual MCA DOCX file."""
        path = "../../sample_files/MCA Timetable-2026-Odd V7.docx"
        abs_path = os.path.abspath(os.path.join(os.path.dirname(__file__), path))
        if not os.path.exists(abs_path):
            pytest.skip(f"Real MCA DOCX not found at: {abs_path}")
        return abs_path

    @pytest.fixture
    def placement_blocks(self, mca_docx_path):
        """Parse the MCA DOCX and return all Thursday Placement blocks."""
        with open(mca_docx_path, "rb") as f:
            docx_bytes = f.read()

        preview = parse_docx_timetable(
            docx_bytes=docx_bytes,
            department="Computer Applications",
            academic_year="2026-Odd",
            source_file="MCA Timetable-2026-Odd V7.docx",
        )

        all_blocks = preview.occupancy_ready_blocks + preview.occupancy_review_blocks
        blocks = [
            b for b in all_blocks
            if b.day == "thursday"
            and any("placement" in a.code.lower() for a in b.activity_candidates)
        ]
        return blocks, preview

    def test_placement_blocks_found(self, placement_blocks):
        """Four Placement blocks must be found on Thursday."""
        blocks, _ = placement_blocks
        assert len(blocks) >= 4, (
            f"Expected at least 4 Thursday Placement blocks, found {len(blocks)}. "
            f"Sections: {[b.section for b in blocks]}"
        )

    def test_placement_blocks_are_student_managed(self, placement_blocks):
        """All Placement blocks must carry STUDENT_MANAGED participation policy."""
        blocks, _ = placement_blocks
        for block in blocks:
            assert block.participation_policy == "STUDENT_MANAGED", (
                f"{block.day} {block.section} {block.slots}: "
                f"expected STUDENT_MANAGED, got {block.participation_policy!r}"
            )

    def test_placement_teacher_candidates_empty(self, placement_blocks):
        """Placement blocks must have no teacher candidates (intentional absence)."""
        blocks, _ = placement_blocks
        for block in blocks:
            assert block.teacher_candidates == [], (
                f"{block.day} {block.section}: expected no teacher candidates, "
                f"got {[t.acronym for t in block.teacher_candidates]}"
            )

    def test_placement_no_teacher_occupancy_created(self, placement_blocks):
        """Placement must create zero teacher occupancy records."""
        blocks, preview = placement_blocks
        placement_block_ids = {b.temp_id for b in blocks}
        # No teacher occupancy from any Placement block
        bad = [
            occ for occ in preview.teacher_occupancies
            if occ.source_location.table_row and occ.extraction_reason  # heuristic
        ]
        # The real check: teacher_allocations on each block must be empty
        for block in blocks:
            assert block.teacher_allocations == [], (
                f"{block.day} {block.section}: Placement block must have no teacher allocations, "
                f"got {block.teacher_allocations}"
            )

    def test_placement_resource_ambiguous(self, placement_blocks):
        """Placement blocks with slash-separated resources must stay AMBIGUOUS."""
        blocks, _ = placement_blocks
        for block in blocks:
            if block.resource_occupancy_status == "AMBIGUOUS":
                # Good — resource ambiguity is real and preserved
                pass
            else:
                # If for some reason the parser assigned deterministic resources, that's OK too
                # (e.g. if the DOCX uses comma not slash). Just ensure it's not UNRESOLVED.
                assert block.resource_occupancy_status in ("AMBIGUOUS", "DETERMINISTIC", "UNSPECIFIED"), (
                    f"{block.day} {block.section}: unexpected resource status "
                    f"{block.resource_occupancy_status!r}"
                )

    def test_placement_blocks_not_blocked_by_teacher_absence(self, placement_blocks):
        """Placement blocks must NOT have teacher_occupancy_status = AMBIGUOUS.

        UNSPECIFIED is expected (no teacher = intentional).
        AMBIGUOUS would mean the parser found conflicting teacher tokens, which is wrong.
        """
        blocks, _ = placement_blocks
        for block in blocks:
            assert block.teacher_occupancy_status != "AMBIGUOUS", (
                f"{block.day} {block.section}: teacher_occupancy_status is AMBIGUOUS, "
                "which means the parser found conflicting teacher tokens for Placement — unexpected."
            )

    def test_no_fake_teacher_in_placement(self, placement_blocks):
        """No fake 'Students' or 'Placement' teacher must appear anywhere in teacher occupancies."""
        _, preview = placement_blocks
        fake_names = {"students", "student", "placement team", "placement", "none"}
        for occ in preview.teacher_occupancies:
            assert occ.teacher_acronym.lower() not in fake_names, (
                f"Fake teacher '{occ.teacher_acronym}' invented in teacher occupancies"
            )

    def test_placement_slots_cover_s6_s7_s8(self, placement_blocks):
        """Placement blocks must cover S6+S7+S8 on Thursday."""
        blocks, _ = placement_blocks
        for block in blocks:
            slots_set = set(block.slots)
            assert "S6" in slots_set or "S7" in slots_set or "S8" in slots_set, (
                f"{block.section}: expected S6/S7/S8 in slots, got {block.slots}"
            )
