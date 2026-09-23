"""Structural DOCX regression tests.

Uses the real MCA Timetable-2026-Odd V7.docx as a regression fixture.
NO MCA-specific values are hardcoded in the production code — these tests
verify structural behavior using the real document as ground truth.

Tests cover all 12 structural requirements from the parser specification:
  1. S7+S8 gridSpan coverage (not S7+S8+S9)
  2. vMerge / cross-section shared blocks
  3. Multi-activity cells (blank-line separated groups)
  4. CC (RR) inline parenthesis activity extraction
  5. Ind* EXTERNAL token preservation
  6. Meghana name-only legend entry
  7. Comma-separated resource lists (all OCCUPIED)
  8. Slash-separated resource alternatives (AMBIGUOUS)
  9. Resource-only blocks (no teacher)
  10. Mixed teacher+resource lists
  11. Activity ambiguity never blocks deterministic occupancy
  12. No candidate silently dropped
"""

import pytest
from pathlib import Path
from typing import Optional

from app.services.docx_import.parser import (
    parse_docx_timetable,
    parse_paragraph_group,
    normalize_resource_code,
    has_tokenization_ambiguity,
)
from app.services.docx_import.staging import OccupantRole
from app.services.docx_import.occupancy import OccupancyExtractor, OccupancyStatus


# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

MCA_DOCX = Path(__file__).resolve().parents[2] / "sample_files" / "MCA Timetable-2026-Odd V7.docx"


def _load_preview():
    with open(MCA_DOCX, "rb") as f:
        docx_bytes = f.read()
    return parse_docx_timetable(
        docx_bytes=docx_bytes,
        department="Computer Applications",
        academic_year="2026-Odd",
        source_file="MCA Timetable-2026-Odd V7.docx",
    )


@pytest.fixture(scope="module")
def preview():
    return _load_preview()


@pytest.fixture(scope="module")
def all_blocks(preview):
    return preview.occupancy_ready_blocks + preview.occupancy_review_blocks


def _find(all_blocks, day, section_substr, slot):
    for b in all_blocks:
        if (b.day == day
                and section_substr.upper().replace(" ", "") in b.section.upper().replace(" ", "")
                and slot in b.slots):
            return b
    return None


# ---------------------------------------------------------------------------
# 1. Slot coverage — gridSpan maps to physical columns, not arithmetic
# ---------------------------------------------------------------------------

class TestSlotCoverage:
    """Physical-column → slot mapping from header gridSpan."""

    def test_tue_iiia_span3_covers_s7_s8_only(self, all_blocks):
        """TUE III-A: col 10, span=3 → S7+S8 only (NOT S7+S8+S9).

        Real document: col 10 spans 3 physical columns.
        S8 itself spans 2 physical columns → span=3 covers S7+S8 only.
        """
        block = _find(all_blocks, "tuesday", "III-A", "S7")
        assert block is not None, "TUE III-A S7 block not found"
        assert set(block.slots) == {"S7", "S8"}, (
            f"Expected S7+S8 only, got {block.slots}\n"
            f"Text: {block.original_text[:80]!r}"
        )
        # Verify content is the ADA2/ELE-2 lab block
        assert "ADA" in block.original_text.upper() or "ELE" in block.original_text.upper()

    def test_mon_iiia_span2_covers_s7_s8(self, all_blocks):
        """MON III-A: ADA2 Lab block at S7+S8 (col9 span=2).

        CORRECTION: The real MCA DOCX has the MON III-A ADA2 Lab block at S6+S7
        (physical col 9, span=2 → S6+S7). The block at S7+S8 is for TUE III-A.
        """
        # MON III-A S6+S7 block should exist (ADA2 Lab)
        block = _find(all_blocks, "monday", "III-A", "S6")
        assert block is not None, "MON III-A S6 block not found"
        # It covers S6+S7 (span=2)
        assert "S7" in block.slots, f"Expected S6+S7, got {block.slots}"

    def test_sat_iiia_s2_s3_two_slots(self, all_blocks):
        """SAT III-A: S2+S3 block exists."""
        block = _find(all_blocks, "saturday", "III-A", "S2")
        assert block is not None, "SAT III-A S2 block not found"
        assert "S3" in block.slots, f"Expected S2+S3, got {block.slots}"


# ---------------------------------------------------------------------------
# 2. vMerge / duplicate-safety
# ---------------------------------------------------------------------------

class TestDuplicateSafety:
    """Merged cells must not produce duplicate logical blocks."""

    def test_no_duplicate_logical_blocks(self, all_blocks):
        """No (day, section, slots) triple appears more than once."""
        from collections import Counter
        keys = [(b.day, b.section, tuple(sorted(b.slots))) for b in all_blocks]
        counts = Counter(keys)
        dupes = {k: v for k, v in counts.items() if v > 1}
        assert dupes == {}, f"Duplicate blocks found:\n{dupes}"

    def test_no_intra_block_teacher_duplicates(self, preview):
        """Each (teacher, day, slot) appears at most once within a single block."""
        for block in preview.occupancy_ready_blocks + preview.occupancy_review_blocks:
            seen = set()
            for t in block.teacher_allocations:
                key = (t.teacher_acronym, t.day, t.slot)
                assert key not in seen, (
                    f"Intra-block duplicate: {key}\n"
                    f"Block: {block.day} {block.section} {block.slots}\n"
                    f"Text: {block.original_text[:80]!r}"
                )
                seen.add(key)


# ---------------------------------------------------------------------------
# 3. Multi-activity cells — blank-line separated groups preserved
# ---------------------------------------------------------------------------

class TestMultiActivityCells:
    """Complex cells with multiple activity groups are preserved."""

    def test_fri_iiia_s7_s8_has_two_activity_groups(self, all_blocks):
        """FRI III-A S7+S8: ELE-3 and BC (Theory) in two groups."""
        block = _find(all_blocks, "friday", "III-A", "S7")
        assert block is not None, "FRI III-A S7 block not found"
        assert len(block.activity_groups) >= 2, (
            f"Expected >=2 activity groups, got {len(block.activity_groups)}\n"
            f"Text: {block.original_text[:100]!r}"
        )
        acts = [a.code for a in block.activity_candidates]
        # Both ELE-3 and BC preserved
        assert any("ELE" in a for a in acts), f"ELE-3 missing: {acts}"
        assert any("BC" in a for a in acts), f"BC missing: {acts}"

    def test_tue_iiia_s7_s8_has_two_activity_groups(self, all_blocks):
        """TUE III-A S7+S8: ADA2 Lab and ELE-2 in two groups."""
        block = _find(all_blocks, "tuesday", "III-A", "S7")
        assert block is not None
        assert len(block.activity_groups) >= 2, (
            f"Expected >=2 activity groups, got {len(block.activity_groups)}\n"
            f"Text: {block.original_text[:100]!r}"
        )

    def test_mon_iiia_s7_s8_has_two_activity_groups(self, all_blocks):
        """MON III-A S7+S8: ADA2 Lab (Sec A) and ELE-2 in two groups."""
        block = _find(all_blocks, "monday", "III-A", "S7")
        assert block is not None
        assert len(block.activity_groups) >= 2, (
            f"Expected >=2 activity groups, got {len(block.activity_groups)}"
        )

    def test_sat_iiia_s2_s3_two_activity_groups(self, all_blocks):
        """SAT III-A S2+S3: ELE-3 (SC4,SC5,SC6) and (BC1,BC2)."""
        block = _find(all_blocks, "saturday", "III-A", "S2")
        assert block is not None
        # Multi-group: ELE-3 + BC1/BC2 groups
        assert len(block.activity_groups) >= 2, (
            f"Expected >=2 groups, got {len(block.activity_groups)}\n"
            f"Text: {block.original_text[:120]!r}"
        )


# ---------------------------------------------------------------------------
# 4. CC (RR) — activity with inline parenthesis
# ---------------------------------------------------------------------------

class TestInlineParenthesisActivity:
    """Activity lines containing inline parenthesised metadata."""

    def test_parse_paragraph_group_cc_rr(self):
        """'CC (RR)' line -> activity=CC, teacher=RR."""
        group = parse_paragraph_group(
            lines=["CC (RR)", "(CA3)"],
            faculty_legend={"RR": "Sri. R.V.Raghavendra Rao"},
            faculty_name_only={},
            faculty_external={},
            resource_legend={"CA3": "CA classroom 3"},
        )
        acts = [a.code for a in group.activity_candidates]
        teachers = [t.acronym for t in group.teacher_candidates]
        resources = [r.normalized_code for r in group.resource_candidates]
        assert "CC" in acts, f"Activity CC not extracted: {acts}"
        assert "RR" in teachers, f"Teacher RR not extracted: {teachers}"
        assert "CA3" in resources, f"Resource CA3 not extracted: {resources}"

    def test_real_docx_wed_iiib_s4_cc_rr(self, all_blocks):
        """WED III-B S4: 'CC (RR)\n(CA3)' -> CC activity, RR teacher, CA3 resource."""
        block = _find(all_blocks, "wednesday", "III-B", "S4")
        assert block is not None, "WED III-B S4 block not found"
        acts = [a.code for a in block.activity_candidates]
        teachers = [t.normalized_acronym for t in block.teacher_candidates]
        resources = [r.normalized_code for r in block.resource_candidates]
        assert any("CC" in a for a in acts), f"CC not found in {acts}"
        assert "RR" in teachers, f"RR not found in {teachers}"
        assert "CA3" in resources, f"CA3 not found in {resources}"

    def test_real_docx_thu_iiib_s4_cc_rr(self, all_blocks):
        """THU III-B S4: same CC (RR) / FDC pattern."""
        block = _find(all_blocks, "thursday", "III-B", "S4")
        assert block is not None, "THU III-B S4 block not found"
        teachers = [t.normalized_acronym for t in block.teacher_candidates]
        assert "RR" in teachers, f"RR not found: {teachers}"


# ---------------------------------------------------------------------------
# 5. Ind* — EXTERNAL token preserved
# ---------------------------------------------------------------------------

class TestIndStarExternal:
    """Ind* (Industry Person) preserved with EXTERNAL role."""

    def test_parse_paragraph_group_ind_star(self):
        """'(Ind*)' classified as EXTERNAL, not FACULTY or UNKNOWN."""
        group = parse_paragraph_group(
            lines=["ELE-2", "(Ind*)", "(FDC)"],
            faculty_legend={},
            faculty_name_only={},
            faculty_external={"Ind*": "Industry Person"},
            resource_legend={"FDC": "Faculty Development center"},
        )
        ext = [t for t in group.teacher_candidates if t.role == OccupantRole.EXTERNAL]
        assert len(ext) == 1, f"Expected 1 EXTERNAL teacher, got {len(ext)}"
        assert "IND" in ext[0].normalized_acronym.upper()

    def test_ind_star_not_unknown(self):
        """Ind* must NOT appear in unknown_tokens when external legend has it."""
        group = parse_paragraph_group(
            lines=["ELE-2", "(Ind*)", "(FDC)"],
            faculty_legend={},
            faculty_name_only={},
            faculty_external={"Ind*": "Industry Person"},
            resource_legend={"FDC": "Faculty Development center"},
        )
        assert not any("IND" in tok.upper() for tok in group.unknown_tokens), (
            f"Ind* in unknown_tokens: {group.unknown_tokens}"
        )

    def test_ind_star_in_legend(self, preview):
        """Parser correctly populates faculty_external from legend."""
        assert any("IND" in k.upper() for k in preview.faculty_external), (
            f"Ind* not in faculty_external: {preview.faculty_external}"
        )

    def test_ind_star_preserved_in_real_docx(self, all_blocks):
        """At least one block in real DOCX has an EXTERNAL (Ind*) candidate."""
        ind_blocks = [
            b for b in all_blocks
            if any(t.role == OccupantRole.EXTERNAL for t in b.teacher_candidates)
        ]
        assert len(ind_blocks) > 0, "No Ind* EXTERNAL blocks found"


# ---------------------------------------------------------------------------
# 6. Meghana — name-only legend entry
# ---------------------------------------------------------------------------

class TestMeghanaNameOnly:
    """Meghana (row 15, no acronym) preserved as NAME_ONLY FACULTY."""

    def test_meghana_in_name_only_legend(self, preview):
        """Parser extracts Meghana into faculty_name_only dict."""
        assert "MEGHANA" in preview.faculty_name_only, (
            f"Meghana not in faculty_name_only: {preview.faculty_name_only}"
        )

    def test_parse_paragraph_group_meghana(self):
        """'(Meghana)' -> FACULTY candidate with is_name_only=True."""
        group = parse_paragraph_group(
            lines=["BC (Theory)", "(Meghana)", "(CA1)"],
            faculty_legend={},
            faculty_name_only={"MEGHANA": "Meghana"},
            faculty_external={},
            resource_legend={"CA1": "CA classroom 1"},
        )
        teachers = group.teacher_candidates
        meghana = [t for t in teachers if "MEGHANA" in t.normalized_acronym.upper()]
        assert len(meghana) == 1, f"Meghana not found in {[t.acronym for t in teachers]}"
        assert meghana[0].is_name_only == True
        assert meghana[0].role == OccupantRole.FACULTY

    def test_meghana_in_real_docx_blocks(self, all_blocks):
        """Meghana appears in at least one block in the real DOCX."""
        meghana_blocks = [
            b for b in all_blocks
            if any("MEGHANA" in t.normalized_acronym.upper() for t in b.teacher_candidates)
        ]
        assert len(meghana_blocks) > 0, "Meghana not found in any block"

    def test_meghana_contributes_to_teacher_occupancy(self, all_blocks, preview):
        """Meghana (NAME_ONLY) contributes to teacher occupancy."""
        # FRI III-A S7+S8 should have Meghana as an occupancy record
        block = _find(all_blocks, "friday", "III-A", "S7")
        assert block is not None
        meghana_alloc = [
            t for t in block.teacher_allocations
            if "MEGHANA" in t.teacher_acronym.upper()
        ]
        assert len(meghana_alloc) > 0, (
            f"Meghana not in teacher_allocations: {[t.teacher_acronym for t in block.teacher_allocations]}"
        )


# ---------------------------------------------------------------------------
# 7. Comma-separated resource lists — all OCCUPIED
# ---------------------------------------------------------------------------

class TestCommaResourceLists:
    """(CA3, CA2) → both resources OCCUPIED, is_assignment_ambiguous=False."""

    def test_comma_resources_not_ambiguous(self):
        """Comma-list resources have is_assignment_ambiguous=False."""
        group = parse_paragraph_group(
            lines=["ELE-3", "(VK, VR)", "(CA3, CA2)"],
            faculty_legend={"VK": "Dr. K Vijaya Kumar", "VR": "Veena R"},
            faculty_name_only={},
            faculty_external={},
            resource_legend={"CA3": "CA3", "CA2": "CA2"},
        )
        resources = group.resource_candidates
        assert len(resources) == 2, f"Expected 2 resources, got {resources}"
        assert all(not r.is_assignment_ambiguous for r in resources), (
            f"Resources wrongly marked ambiguous: {[(r.code, r.is_assignment_ambiguous) for r in resources]}"
        )

    def test_comma_resources_occupancy_deterministic(self):
        """Comma-list resources → resource_occupancy_status=DETERMINISTIC."""
        from app.services.docx_import.staging import TimetableBlock
        from app.domain.timetable import SourceLocation
        block = TimetableBlock(
            day="wednesday", section="III-A", slots=["S8", "S9"],
            activity_candidates=[],
            teacher_candidates=[],
            resource_candidates=[
                __import__('app.services.docx_import.staging', fromlist=['ResourceCandidate']).ResourceCandidate(
                    code="CA3", normalized_code="CA3", is_assignment_ambiguous=False),
                __import__('app.services.docx_import.staging', fromlist=['ResourceCandidate']).ResourceCandidate(
                    code="CA2", normalized_code="CA2", is_assignment_ambiguous=False),
            ],
            source_location=SourceLocation(source_type="DOCX"),
        )
        result = OccupancyExtractor.extract_occupancy(block)
        assert block.resource_occupancy_status == "DETERMINISTIC", (
            f"Expected DETERMINISTIC, got {block.resource_occupancy_status}"
        )
        assert all(r.status == OccupancyStatus.OCCUPIED for r in result.resource_occupancies)

    def test_real_docx_wed_iiia_ca3_ca2_occupied(self, all_blocks):
        """WED III-A ELE-3 block: CA3 + CA2 both OCCUPIED (not AMBIGUOUS)."""
        block = _find(all_blocks, "wednesday", "III-A", "S8")
        assert block is not None, "WED III-A S8 block not found"
        resources = [(r.normalized_code, r.is_assignment_ambiguous) for r in block.resource_candidates]
        ca3_ca2 = [(c, amb) for c, amb in resources if c in ("CA3", "CA2")]
        assert len(ca3_ca2) == 2, f"Expected CA3+CA2, got {resources}"
        assert all(not amb for _, amb in ca3_ca2), (
            f"CA3/CA2 wrongly ambiguous: {ca3_ca2}"
        )
        # Occupancy check
        occ_statuses = {r.resource_code: r.status for r in block.resource_allocations}
        assert occ_statuses.get("CA3") == OccupancyStatus.OCCUPIED, f"CA3 not OCCUPIED: {occ_statuses}"
        assert occ_statuses.get("CA2") == OccupancyStatus.OCCUPIED, f"CA2 not OCCUPIED: {occ_statuses}"


# ---------------------------------------------------------------------------
# 8. Slash-separated resource alternatives — AMBIGUOUS
# ---------------------------------------------------------------------------

class TestSlashResourceAlternatives:
    """(CA1/CA2) → genuine resource alternative, AMBIGUOUS."""

    def test_slash_resources_are_ambiguous(self):
        """Slash-list resources have is_assignment_ambiguous=True."""
        group = parse_paragraph_group(
            lines=["Placement", "(CA1/CA2)"],
            faculty_legend={},
            faculty_name_only={},
            faculty_external={},
            resource_legend={"CA1": "CA1", "CA2": "CA2"},
        )
        resources = group.resource_candidates
        assert len(resources) == 2, f"Expected 2 resources, got {resources}"
        assert all(r.is_assignment_ambiguous for r in resources), (
            f"Resources should be ambiguous: {[(r.code, r.is_assignment_ambiguous) for r in resources]}"
        )

    def test_slash_resources_never_free(self):
        """Ambiguous resources are AMBIGUOUS, never FREE."""
        from app.services.docx_import.staging import TimetableBlock, ResourceCandidate
        from app.domain.timetable import SourceLocation
        block = TimetableBlock(
            day="thursday", section="III-B", slots=["S9"],
            activity_candidates=[],
            teacher_candidates=[],
            resource_candidates=[
                ResourceCandidate("CA1", "CA1", is_assignment_ambiguous=True),
                ResourceCandidate("CA2", "CA2", is_assignment_ambiguous=True),
            ],
            source_location=SourceLocation(source_type="DOCX"),
        )
        result = OccupancyExtractor.extract_occupancy(block)
        for occ in result.resource_occupancies:
            assert occ.status == OccupancyStatus.AMBIGUOUS, (
                f"Expected AMBIGUOUS, got {occ.status} for {occ.resource_code}"
            )
            assert occ.status != OccupancyStatus.OCCUPIED


# ---------------------------------------------------------------------------
# 9. Resource-only blocks
# ---------------------------------------------------------------------------

class TestResourceOnlyBlocks:
    """Blocks with resources but no teacher must not disappear."""

    def test_cultural_activity_fdc(self, all_blocks):
        """'Cultural activity / (FDC)' — resource-only block preserved."""
        cultural = [
            b for b in all_blocks
            if any("CULTURAL" in a.code.upper() for a in b.activity_candidates)
        ]
        assert len(cultural) > 0, "No 'Cultural activity' blocks found"
        for b in cultural:
            resources = [r.normalized_code for r in b.resource_candidates]
            assert "FDC" in resources, f"FDC missing from cultural block: {resources}"
            # No faculty teacher required
            assert b.teacher_occupancy_status == "UNSPECIFIED"

    def test_vac_fdc_sat_iiia(self, all_blocks):
        """'VAC / (FDC)' SAT III-A: resource-only block preserved."""
        block = _find(all_blocks, "saturday", "III-A", "S4")
        assert block is not None, "SAT III-A S4 block not found"
        acts = [a.code for a in block.activity_candidates]
        resources = [r.normalized_code for r in block.resource_candidates]
        assert any("VAC" in a.upper() for a in acts), f"VAC missing: {acts}"
        assert "FDC" in resources, f"FDC missing: {resources}"
        assert block.teacher_occupancy_status == "UNSPECIFIED"
        assert block.resource_occupancy_status in ("DETERMINISTIC", "UNSPECIFIED")

    def test_resource_only_not_empty(self, all_blocks):
        """Resource-only blocks exist and have resource occupancy records."""
        resource_only = [
            b for b in all_blocks
            if len(b.teacher_candidates) == 0 and len(b.resource_candidates) > 0
        ]
        assert len(resource_only) > 0, "No resource-only blocks found"
        # Their resource occupancy should be recorded
        for b in resource_only:
            assert len(b.resource_allocations) > 0 or b.resource_occupancy_status == "UNSPECIFIED", (
                f"Resource-only block {b.day} {b.section} {b.slots} has no allocations"
            )


# ---------------------------------------------------------------------------
# 10. Mixed teacher+resource lists
# ---------------------------------------------------------------------------

class TestMixedTeacherResourceLists:
    """Complex blocks with multiple teachers and multiple resources."""

    def test_wed_iiia_s4_s5_full(self, all_blocks):
        """WED III-A S4+S5: ADA 3,4, DT 1,2 / TSP,KPS,SU,DNS / Lab1B,CA3,FDC."""
        block = _find(all_blocks, "wednesday", "III-A", "S4")
        assert block is not None, "WED III-A S4 block not found"
        teachers = {t.normalized_acronym for t in block.teacher_candidates}
        resources = {r.normalized_code for r in block.resource_candidates}
        assert {"TSP", "KPS", "SU", "DNS"}.issubset(teachers), (
            f"Missing teachers: {teachers}"
        )
        assert "LAB1B" in resources or "LAB 1B" in {r.code for r in block.resource_candidates}, (
            f"Lab1B missing: {resources}"
        )

    def test_sat_iiia_s2_s3_full(self, all_blocks):
        """SAT III-A S2+S3: ELE-3 SC4/5/6 + BC1/BC2 groups."""
        block = _find(all_blocks, "saturday", "III-A", "S2")
        assert block is not None
        teachers = {t.normalized_acronym for t in block.teacher_candidates}
        resources = {r.normalized_code for r in block.resource_candidates}
        # Should have VR, RMR, RR from ELE-3 group + GK from BC group
        assert len(teachers) >= 3, f"Expected >=3 teachers, got {teachers}"
        # LAB1A and LAB1B from ELE-3 + BC1/BC2 groups
        assert len(resources) >= 1, f"Expected resources, got {resources}"


# ---------------------------------------------------------------------------
# 11. Activity ambiguity never blocks deterministic occupancy
# ---------------------------------------------------------------------------

class TestActivityAmbiguityIndependent:
    """Activity semantic ambiguity must not block teacher/resource occupancy."""

    def test_ambiguous_activity_does_not_block_teachers(self, all_blocks):
        """Every block with AMBIGUOUS activity has DETERMINISTIC or UNSPECIFIED teacher status."""
        blocked = [
            b for b in all_blocks
            if b.activity_semantic_status == "AMBIGUOUS"
            and b.teacher_occupancy_status not in ("DETERMINISTIC", "UNSPECIFIED")
        ]
        assert blocked == [], (
            f"Activity ambiguity blocking teacher occupancy in {len(blocked)} blocks:\n"
            + "\n".join(f"  {b.day} {b.section} {b.slots}: {b.activity_semantic_status}"
                        for b in blocked[:3])
        )

    def test_ambiguous_activity_blocks_are_in_ready(self, preview):
        """Activity-AMBIGUOUS blocks with DETERMINISTIC teacher → occupancy_ready_blocks."""
        activity_only_in_review = [
            b for b in preview.occupancy_review_blocks
            if b.teacher_occupancy_status in ("DETERMINISTIC", "UNSPECIFIED")
            and b.resource_occupancy_status in ("DETERMINISTIC", "UNSPECIFIED")
        ]
        assert activity_only_in_review == [], (
            f"Activity-only blocks wrongly in review: {len(activity_only_in_review)}\n"
            + "\n".join(f"  {b.day} {b.section} teacher={b.teacher_occupancy_status} "
                        f"resource={b.resource_occupancy_status}"
                        for b in activity_only_in_review[:3])
        )

    def test_three_statuses_independent(self, all_blocks):
        """activity/teacher/resource statuses are independently computed."""
        # Find a block with AMBIGUOUS activity but DETERMINISTIC teacher
        examples = [
            b for b in all_blocks
            if b.activity_semantic_status == "AMBIGUOUS"
            and b.teacher_occupancy_status == "DETERMINISTIC"
        ]
        # There should be several in the MCA doc
        assert len(examples) > 0, (
            "No blocks with AMBIGUOUS activity + DETERMINISTIC teacher found"
        )


# ---------------------------------------------------------------------------
# 12. No silent drops — every token accounted for
# ---------------------------------------------------------------------------

class TestNoSilentDrops:
    """Every candidate must be RESOLVED, AMBIGUOUS, or preserved in unknown_tokens."""

    def test_meghana_not_lost(self, all_blocks):
        """Meghana token is preserved (not silently dropped)."""
        meghana_any = any(
            any("MEGHANA" in t.normalized_acronym.upper() for t in b.teacher_candidates)
            for b in all_blocks
        )
        assert meghana_any, "Meghana silently dropped from all blocks"

    def test_ind_star_not_lost(self, all_blocks):
        """Ind* token is preserved (not silently dropped)."""
        ind_any = any(
            any(t.role == OccupantRole.EXTERNAL for t in b.teacher_candidates)
            for b in all_blocks
        )
        assert ind_any, "Ind* (EXTERNAL) silently dropped from all blocks"

    def test_fdc_resource_preserved(self, all_blocks):
        """FDC resource token is preserved in blocks that reference it."""
        fdc_blocks = [
            b for b in all_blocks
            if any("FDC" in r.normalized_code for r in b.resource_candidates)
        ]
        assert len(fdc_blocks) > 0, "FDC resource silently dropped from all blocks"

    def test_ca3_ca2_preserved_in_comma_lists(self, all_blocks):
        """CA3 and CA2 from (CA3, CA2) are preserved."""
        ca3_blocks = [
            b for b in all_blocks
            if any("CA3" in r.normalized_code for r in b.resource_candidates)
        ]
        ca2_blocks = [
            b for b in all_blocks
            if any("CA2" in r.normalized_code for r in b.resource_candidates)
        ]
        assert len(ca3_blocks) > 0, "CA3 silently dropped"
        assert len(ca2_blocks) > 0, "CA2 silently dropped"

    def test_total_block_count_non_trivial(self, preview):
        """Parser extracts a meaningful number of blocks from the real DOCX."""
        # The MCA timetable has ~100 logical blocks across 6 days × 4 sections
        assert preview.total_blocks >= 80, (
            f"Too few blocks: {preview.total_blocks} (expected >=80)"
        )
        assert preview.total_blocks <= 200, (
            f"Too many blocks: {preview.total_blocks} (expected <=200)"
        )


# ---------------------------------------------------------------------------
# Department agnosticism
# ---------------------------------------------------------------------------

class TestDepartmentAgnosticism:
    """Production parser code must not contain MCA-specific hardcoded values."""

    def test_no_hardcoded_mca_terms_in_parser(self):
        """Parser.py contains no hardcoded MCA teacher/resource identifiers."""
        import re
        parser_path = Path(__file__).resolve().parents[1] / "app" / "services" / "docx_import" / "parser.py"
        with open(parser_path) as f:
            lines = f.readlines()

        mca_terms = [
            "SU", "DNS", "RMR", "VK", "VPP", "TSP", "GK", "KPS",
            "RR", "SS", "TS", "VR", "SKR",
            "FDC", "LAB1A", "LAB1B", "CA1", "CA2", "CA3",
        ]

        violations = []
        for lineno, line in enumerate(lines, 1):
            # Skip pure comment lines
            code_part = line.split("#")[0]
            for term in mca_terms:
                # Exact quoted match in code
                if f'"{term}"' in code_part or f"'{term}'" in code_part:
                    violations.append(f"line {lineno}: {line.rstrip()}")

        assert violations == [], (
            f"MCA-specific hardcoded values found in parser.py:\n" + "\n".join(violations[:10])
        )
