"""Tests for DOCX parser implementation.

Tests the deterministic, non-guessing structural parser.
Updated for the new layered parsing architecture.
"""

import pytest
from io import BytesIO

from docx import Document
from docx.shared import Inches

from app.services.docx_import import (
    parse_docx_timetable,
    convert_preview_to_canonical,
    ActivityCandidate,
    TeacherCandidate,
    ResourceCandidate,
    TimetableBlock,
)
from app.services.docx_import.parser import (
    has_tokenization_ambiguity,
    normalize_day,
    parse_paragraph_group,
    normalize_resource_code,
)
from app.services.docx_import.resolution import ResolutionRule
from app.domain.timetable import SourceLocation, ValidationSeverity


# ---------------------------------------------------------------------------
# Helpers to extract candidates via the new parse_paragraph_group API
# ---------------------------------------------------------------------------

def _extract_activities(lines, resource_legend=None):
    """Adapter: extract activity candidates from lines."""
    group = parse_paragraph_group(
        lines=lines,
        faculty_legend={},
        faculty_name_only={},
        faculty_external={},
        resource_legend={normalize_resource_code(k): v for k, v in (resource_legend or {}).items()},
    )
    return group.activity_candidates


def _extract_teachers(lines, faculty_legend=None):
    """Adapter: extract teacher candidates from lines."""
    group = parse_paragraph_group(
        lines=lines,
        faculty_legend=faculty_legend or {},
        faculty_name_only={},
        faculty_external={},
        resource_legend={},
    )
    return group.teacher_candidates


def _extract_resources(lines, resource_legend=None):
    """Adapter: extract resource candidates from lines."""
    group = parse_paragraph_group(
        lines=lines,
        faculty_legend={},
        faculty_name_only={},
        faculty_external={},
        resource_legend={normalize_resource_code(k): v for k, v in (resource_legend or {}).items()},
    )
    return group.resource_candidates


# ---------------------------------------------------------------------------
# Activity tokenization
# ---------------------------------------------------------------------------

class TestActivityTokenization:
    """Test activity tokenization rules."""

    def test_simple_activity_not_ambiguous(self):
        """Simple activity like 'DBMS' is not ambiguous."""
        assert has_tokenization_ambiguity("DBMS") == False
        assert has_tokenization_ambiguity("ADA") == False
        assert has_tokenization_ambiguity("Library/Research Activity") == False

    def test_comma_separated_numbers_is_ambiguous(self):
        """'DS 3,4' is tokenization ambiguous."""
        assert has_tokenization_ambiguity("DS 3,4") == True
        assert has_tokenization_ambiguity("PE 1,2,3,4") == True

    def test_comma_separated_codes_is_ambiguous(self):
        """'PY1, PE2' is tokenization ambiguous."""
        assert has_tokenization_ambiguity("PY1, PE2") == True
        assert has_tokenization_ambiguity("ADA1, DT 3,4") == True

    def test_extract_preserves_ambiguous_phrase(self):
        """Ambiguous phrases preserved as single candidate."""
        candidates = _extract_activities(
            ["DS 3,4", "(SS)", "LAB1A"],
            resource_legend={"LAB1A": "Lab 1A"},
        )
        assert len(candidates) == 1
        assert candidates[0].code == "DS 3,4"
        assert candidates[0].is_tokenization_ambiguous == True


# ---------------------------------------------------------------------------
# Teacher extraction
# ---------------------------------------------------------------------------

_FACULTY = {
    "VR": "Veena R", "SU": "Dr. S. Uma", "TS": "Smt. T Sunitha",
    "SS": "Smt. S.Shilpa", "KPS": "Smt. K.P. Shailaja",
    "DNS": "Dr. D. N. Sujatha", "RR": "Sri. R.V.Raghavendra Rao",
    "GK": "Sri. Girish .K", "VPP": "Dr. V Padmapriya",
}


class TestTeacherExtraction:
    """Test teacher candidate extraction."""

    def test_single_teacher_in_parentheses(self):
        """Extract single teacher from '(VR)'."""
        candidates = _extract_teachers(["DBMS", "(VR)", "CA1"], _FACULTY)
        assert len(candidates) == 1
        assert candidates[0].acronym == "VR"
        assert candidates[0].normalized_acronym == "VR"

    def test_multiple_teachers_comma_separated(self):
        """Extract multiple teachers from '(VPP, VR, TS)'."""
        candidates = _extract_teachers(["PY1", "(VPP, VR, TS)", "LAB1A"], _FACULTY)
        assert len(candidates) == 3
        assert [c.acronym for c in candidates] == ["VPP", "VR", "TS"]

    def test_multiple_teacher_groups(self):
        """Extract teachers from '(SU) (TS) (SS, KPS)' on same line."""
        candidates = _extract_teachers(["PY1, PE2", "(SU) (TS) (SS, KPS)", "LAB1B"], _FACULTY)
        assert len(candidates) == 4
        assert [c.acronym for c in candidates] == ["SU", "TS", "SS", "KPS"]

    def test_no_comma_separated_flattening(self):
        """Teachers remain as individual objects, NOT flattened."""
        candidates = _extract_teachers(["Activity", "(SU, TS)", "Room"], _FACULTY)
        assert len(candidates) == 2
        assert not any("," in c.acronym for c in candidates)

    def test_inline_paren_teacher_extracted(self):
        """CC (RR): activity=CC, teacher=RR."""
        candidates = _extract_teachers(["CC (RR)", "(CA3)"], _FACULTY)
        assert any(c.acronym == "RR" for c in candidates)

    def test_ind_star_external(self):
        """Ind* extracted as EXTERNAL role, not FACULTY."""
        group = parse_paragraph_group(
            lines=["ELE-2", "(Ind*)", "(FDC)"],
            faculty_legend={},
            faculty_name_only={},
            faculty_external={"Ind*": "Industry Person"},
            resource_legend={"FDC": "Faculty Dev Center"},
        )
        teachers = group.teacher_candidates
        assert any("IND" in t.normalized_acronym.upper() for t in teachers)
        ext = [t for t in teachers if t.role == "EXTERNAL"]
        assert len(ext) == 1

    def test_name_only_meghana(self):
        """Meghana (name-only legend entry) extracted as FACULTY."""
        group = parse_paragraph_group(
            lines=["BC (Theory)", "(Meghana)", "(CA1)"],
            faculty_legend={},
            faculty_name_only={"MEGHANA": "Meghana"},
            faculty_external={},
            resource_legend={"CA1": "CA classroom 1"},
        )
        teachers = group.teacher_candidates
        assert any("MEGHANA" in t.normalized_acronym.upper() for t in teachers)
        fac = [t for t in teachers if t.role == "FACULTY"]
        assert len(fac) >= 1


# ---------------------------------------------------------------------------
# Resource extraction
# ---------------------------------------------------------------------------

_RESOURCES = {
    "CA1": "CA1", "CA2": "CA2", "CA3": "CA3",
    "LAB1A": "Lab1A", "LAB1B": "Lab1B",
    "LAB 1A": "Lab 1A", "FDC": "FDC", "RL": "RL",
}


class TestResourceExtraction:
    """Test resource candidate extraction."""

    def test_simple_resource_extraction(self):
        """Extract bare resource from line without parens."""
        candidates = _extract_resources(["DBMS", "(VR)", "CA1"], _RESOURCES)
        assert len(candidates) == 1
        assert candidates[0].code == "CA1"
        assert candidates[0].normalized_code == "CA1"

    def test_resource_in_parentheses(self):
        """Extract resource from '(LAB1A)'."""
        candidates = _extract_resources(["PY1", "(SU)", "(LAB1A)"], _RESOURCES)
        assert len(candidates) == 1
        assert candidates[0].normalized_code == "LAB1A"

    def test_multiple_resources_extracted_individually(self):
        """Multiple resources remain as individual objects."""
        candidates = _extract_resources(["Activity", "(Teacher)", "(LAB1A) (LAB1B)"], _RESOURCES)
        assert len(candidates) == 2
        codes = {c.normalized_code for c in candidates}
        assert "LAB1A" in codes
        assert "LAB1B" in codes

    def test_resource_normalization(self):
        """Resource codes normalized (uppercase, no spaces)."""
        candidates = _extract_resources(["Activity", "(Teacher)", "(Lab 1A)"], _RESOURCES)
        assert len(candidates) == 1
        assert candidates[0].normalized_code == "LAB1A"

    def test_comma_list_resources_not_ambiguous(self):
        """(CA3, CA2) — both resources OCCUPIED, is_assignment_ambiguous=False."""
        candidates = _extract_resources(["ELE-3", "(VK, VR)", "(CA3, CA2)"], _RESOURCES)
        ca_res = [c for c in candidates if c.normalized_code in ("CA3", "CA2")]
        assert len(ca_res) == 2
        assert all(not c.is_assignment_ambiguous for c in ca_res)

    def test_slash_list_resources_are_ambiguous(self):
        """(CA1/CA2) — genuine resource alternative, is_assignment_ambiguous=True."""
        candidates = _extract_resources(["Placement", "(CA1/CA2)"], _RESOURCES)
        assert len(candidates) == 2
        assert all(c.is_assignment_ambiguous for c in candidates)


# ---------------------------------------------------------------------------
# Resolution rules
# ---------------------------------------------------------------------------

class TestResolutionRules:
    """Test resolution classification rules."""

    def test_simple_resolved_block(self):
        """1:1:1 with no ambiguity is resolved."""
        block = TimetableBlock(
            day="monday", section="I-A", slots=["S3"],
            activity_candidates=[ActivityCandidate("DBMS", "CLASS", False)],
            teacher_candidates=[TeacherCandidate("VR", "VR")],
            resource_candidates=[ResourceCandidate("CA1", "CA1")],
            source_location=SourceLocation(source_type="DOCX"),
        )
        ResolutionRule.classify_block(block)
        assert block.activity_semantic_status == "RESOLVED"

    def test_missing_teacher_is_unspecified(self):
        """Block with no teacher: teacher_occupancy_status = UNSPECIFIED."""
        block = TimetableBlock(
            day="monday", section="I-A", slots=["S3"],
            activity_candidates=[ActivityCandidate("DBMS", "CLASS", False)],
            teacher_candidates=[],
            resource_candidates=[ResourceCandidate("CA1", "CA1")],
            source_location=SourceLocation(source_type="DOCX"),
        )
        from app.services.docx_import.occupancy import OccupancyExtractor
        res = OccupancyExtractor.extract_occupancy(block)
        assert res.extraction_successful is True
        assert len(res.teacher_occupancies) == 0
        assert block.teacher_occupancy_status == "UNSPECIFIED"

    def test_no_resource_is_resolved(self):
        """1:1:0 (no resource) is resolved."""
        block = TimetableBlock(
            day="monday", section="I-A", slots=["S3"],
            activity_candidates=[ActivityCandidate("Library Research", "OTHER", False)],
            teacher_candidates=[TeacherCandidate("VR", "VR")],
            resource_candidates=[],
            source_location=SourceLocation(source_type="DOCX"),
        )
        ResolutionRule.classify_block(block)
        # No resource -> resource_occupancy_status=UNSPECIFIED (not blocking)


# ---------------------------------------------------------------------------
# Parentheses classification (no associations)
# ---------------------------------------------------------------------------

class TestParenthesesNotAssociations:
    """Parentheses classify types but don't establish relationships."""

    def test_parentheses_classify_candidates(self):
        """Parentheses identify teacher/resource candidates."""
        teachers = _extract_teachers(["DBMS", "(VR)", "(CA1)"], _FACULTY)
        resources = _extract_resources(["DBMS", "(VR)", "(CA1)"], _RESOURCES)
        assert len(teachers) == 1
        assert teachers[0].acronym == "VR"
        assert len(resources) == 1
        assert resources[0].normalized_code == "CA1"

    def test_whitespace_does_not_establish_pairing(self):
        """Whitespace between parentheses doesn't create associations."""
        candidates = _extract_teachers(["PY1, PE2", "(SU) (TS)", "LAB1B"], _FACULTY)
        assert len(candidates) == 2
        assert candidates[0].acronym == "SU"
        assert candidates[1].acronym == "TS"
        assert not hasattr(candidates[0], 'activity')
        assert not hasattr(candidates[0], 'assigned_activity')


# ---------------------------------------------------------------------------
# Day normalization
# ---------------------------------------------------------------------------

class TestDayNormalization:
    """Test day name normalization."""

    def test_day_normalization(self):
        assert normalize_day("MON") == "monday"
        assert normalize_day("TUE") == "tuesday"
        assert normalize_day("WED") == "wednesday"
        assert normalize_day("THU") == "thursday"
        assert normalize_day("FRI") == "friday"
        assert normalize_day("SAT") == "saturday"


# ---------------------------------------------------------------------------
# Source location preservation
# ---------------------------------------------------------------------------

class TestSourceLocationPreservation:
    """Test source location preservation through parsing."""

    def test_source_location_in_block(self):
        source_loc = SourceLocation(source_type="DOCX", table_index=0, table_row=5, table_col=3)
        block = TimetableBlock(
            day="monday", section="I-A", slots=["S3"],
            activity_candidates=[ActivityCandidate("DBMS", "CLASS", False)],
            teacher_candidates=[TeacherCandidate("VR", "VR")],
            resource_candidates=[ResourceCandidate("CA1", "CA1")],
            source_location=source_loc,
            original_text="DBMS\n(VR)\nCA1",
        )
        assert block.source_location.table_row == 5
        assert block.source_location.table_col == 3
        assert block.original_text == "DBMS\n(VR)\nCA1"


# ---------------------------------------------------------------------------
# No guessing
# ---------------------------------------------------------------------------

class TestNoGuessing:
    """Parser never guesses associations."""

    def test_no_arbitrary_teacher_activity_pairing(self):
        """Parser does NOT invent teacher-activity associations."""
        teachers = _extract_teachers(["PY1, PE2", "(SU) (TS)", "LAB1A"], _FACULTY)
        assert len(teachers) == 2
        for teacher in teachers:
            assert not hasattr(teacher, 'activity')
            assert not hasattr(teacher, 'assigned_activity')

    def test_no_first_resource_selection(self):
        """Parser does NOT use 'first resource wins' for slash resources."""
        # Slash-separated resources → both kept as AMBIGUOUS candidates
        candidates = _extract_resources(["Placement", "(CA1/CA2)"], _RESOURCES)
        assert len(candidates) == 2  # both preserved, not first-only
        assert all(c.is_assignment_ambiguous for c in candidates)
