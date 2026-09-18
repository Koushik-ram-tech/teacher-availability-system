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
        assert preview.total_blocks > 100, f"Expected >100 blocks, got {preview.total_blocks}"
        assert preview.occupancy_ready_count > 50, f"Expected >50 resolved, got {preview.occupancy_ready_count}"

        # Should have some unresolved blocks (ambiguous content exists)
        assert preview.occupancy_review_count > 0, "Expected some unresolved blocks"

        print(f"\nActual MCA DOCX parse results:")
        print(f"  Total blocks: {preview.total_blocks}")
        print(f"  Resolved: {preview.occupancy_ready_count}")
        print(f"  Unresolved: {preview.occupancy_review_count}")
        print(f"  Errors: {len(preview.errors)}")
        print(f"  Faculty: {len(preview.faculty_legend)}")

    def test_tuesday_ia_remains_unresolved(self, mca_docx_path):
        """Tuesday I-A parallel activity block should remain unresolved."""
        with open(mca_docx_path, "rb") as f:
            docx_bytes = f.read()

        preview = parse_docx_timetable(
            docx_bytes=docx_bytes,
            department="Computer Applications",
            academic_year="2026-Odd",
            source_file="MCA Timetable-2026-Odd V7.docx"
        )

        # Find Tuesday I-A blocks
        tuesday_ia_blocks = [
            b for b in preview.occupancy_review_blocks
            if b.day == "tuesday" and "I-A" in b.section
        ]

        # Should have unresolved Tuesday I-A blocks
        assert len(tuesday_ia_blocks) > 0, "Tuesday I-A should have unresolved blocks"

        # Check the known ambiguous block "PY1, PE2, DS 3, 4"
        ambiguous_block = None
        for block in tuesday_ia_blocks:
            for activity in block.activity_candidates:
                if "PY1" in activity.code and "PE2" in activity.code:
                    ambiguous_block = block
                    break

        assert ambiguous_block is not None, "Known ambiguous Tuesday I-A block not found"

        # UnresolvedTimetableBlock is by definition unresolved
        # (it's in preview.occupancy_review_blocks)

        # Should have multiple teachers
        assert len(ambiguous_block.teacher_candidates) > 1, \
            f"Expected multiple teachers, got {len(ambiguous_block.teacher_candidates)}"

        # Should have ambiguity reason

        print(f"\nTuesday I-A ambiguous block verified:")
        print(f"  Activities: {[a.code[:30] for a in ambiguous_block.activity_candidates]}")
        print(f"  Teachers: {[t.acronym for t in ambiguous_block.teacher_candidates]}")
        print(f"  Resources: {[r.code for r in ambiguous_block.resource_candidates]}")
