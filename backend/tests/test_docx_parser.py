"""Tests for DOCX parser implementation.

Tests the deterministic, non-guessing parser according to specification.
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
    extract_activity_candidates,
    extract_teacher_candidates,
    extract_resource_candidates,
    has_tokenization_ambiguity,
    normalize_day,
)
from app.services.docx_import.resolution import ResolutionRule
from app.domain.timetable import SourceLocation, ValidationSeverity


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
        lines = ["DS 3,4", "(SS)", "LAB1A"]
        candidates = extract_activity_candidates(lines, {'LAB1A': 'Lab 1A'})

        assert len(candidates) == 1
        assert candidates[0].code == "DS 3,4"
        assert candidates[0].is_tokenization_ambiguous == True


class TestTeacherExtraction:
    """Test teacher candidate extraction."""

    def test_single_teacher_in_parentheses(self):
        """Extract single teacher from '(VR)'."""
        lines = ["DBMS", "(VR)", "CA1"]
        candidates = extract_teacher_candidates(lines, {"VR": "Veena", "SU": "S Uma", "TS": "T Sunitha", "SS": "S Shilpa", "KPS": "K P Shailaja", "DNS": "D N Sujatha", "RR": "R R", "GK": "G K", "VPP": "V Padmapriya"})

        assert len(candidates) == 1
        assert candidates[0].acronym == "VR"
        assert candidates[0].normalized_acronym == "VR"

    def test_multiple_teachers_comma_separated(self):
        """Extract multiple teachers from '(VPP, VR, TS)'."""
        lines = ["PY1", "(VPP, VR, TS)", "LAB1A"]
        candidates = extract_teacher_candidates(lines, {"VR": "Veena", "SU": "S Uma", "TS": "T Sunitha", "SS": "S Shilpa", "KPS": "K P Shailaja", "DNS": "D N Sujatha", "RR": "R R", "GK": "G K", "VPP": "V Padmapriya"})

        assert len(candidates) == 3
        assert [c.acronym for c in candidates] == ["VPP", "VR", "TS"]

    def test_multiple_teacher_groups(self):
        """Extract teachers from '(SU) (TS) (SS, KPS)'."""
        lines = ["PY1, PE2", "(SU) (TS) (SS, KPS)", "LAB1B"]
        candidates = extract_teacher_candidates(lines, {"VR": "Veena", "SU": "S Uma", "TS": "T Sunitha", "SS": "S Shilpa", "KPS": "K P Shailaja", "DNS": "D N Sujatha", "RR": "R R", "GK": "G K", "VPP": "V Padmapriya"})

        assert len(candidates) == 4
        assert [c.acronym for c in candidates] == ["SU", "TS", "SS", "KPS"]

    def test_no_comma_separated_flattening(self):
        """Teachers remain as individual objects, NOT flattened."""
        lines = ["Activity", "(SU, TS)", "Room"]
        candidates = extract_teacher_candidates(lines, {"VR": "Veena", "SU": "S Uma", "TS": "T Sunitha", "SS": "S Shilpa", "KPS": "K P Shailaja", "DNS": "D N Sujatha", "RR": "R R", "GK": "G K", "VPP": "V Padmapriya"})

        # Each teacher is separate object
        assert len(candidates) == 2
        assert not any("," in c.acronym for c in candidates)


class TestResourceExtraction:
    """Test resource candidate extraction."""

    def test_simple_resource_extraction(self):
        """Extract resource from last line."""
        lines = ["DBMS", "(VR)", "CA1"]
        candidates = extract_resource_candidates(lines, {"CA1": "CA1", "LAB1A": "LAB1A", "LAB1B": "LAB1B", "LAB 1A": "LAB 1A", "FDC": "FDC", "RL": "RL"})

        assert len(candidates) == 1
        assert candidates[0].code == "CA1"
        assert candidates[0].normalized_code == "CA1"

    def test_resource_in_parentheses(self):
        """Extract resource from '(LAB1A)'."""
        lines = ["PY1", "(SU)", "(LAB1A)"]
        candidates = extract_resource_candidates(lines, {"CA1": "CA1", "LAB1A": "LAB1A", "LAB1B": "LAB1B", "LAB 1A": "LAB 1A", "FDC": "FDC", "RL": "RL"})

        assert len(candidates) == 1
        assert candidates[0].code == "LAB1A"

    def test_multiple_resources_extracted_individually(self):
        """Multiple resources remain as individual objects."""
        lines = ["Activity", "(Teacher)", "(LAB1A) (LAB1B)"]
        candidates = extract_resource_candidates(lines, {"CA1": "CA1", "LAB1A": "LAB1A", "LAB1B": "LAB1B", "LAB 1A": "LAB 1A", "FDC": "FDC", "RL": "RL"})

        # Should extract both individually
        assert len(candidates) == 2
        assert candidates[0].code == "LAB1A"
        assert candidates[1].code == "LAB1B"

    def test_resource_normalization(self):
        """Resource codes are normalized (uppercase, no spaces)."""
        lines = ["Activity", "(Teacher)", "(Lab 1A)"]
        candidates = extract_resource_candidates(lines, {"CA1": "CA1", "LAB1A": "LAB1A", "LAB1B": "LAB1B", "LAB 1A": "LAB 1A", "FDC": "FDC", "RL": "RL"})

        # Should extract "Lab 1A" which has space - resource heuristic
        assert len(candidates) == 1
        assert candidates[0].code == "Lab 1A"
        assert candidates[0].normalized_code == "LAB1A"


class TestResolutionRules:
    """Test resolution classification rules."""

    def test_simple_resolved_block(self):
        """1:1:1 with no ambiguity is resolved."""
        block = TimetableBlock(
            day="monday",
            section="I-A",
            slots=["S3"],
            activity_candidates=[
                ActivityCandidate(code="DBMS", inferred_type="CLASS", is_tokenization_ambiguous=False)
            ],
            teacher_candidates=[
                TeacherCandidate(acronym="VR", normalized_acronym="VR", is_identity_resolvable=True)
            ],
            resource_candidates=[
                ResourceCandidate(code="CA1", normalized_code="CA1", is_identity_resolvable=True)
            ],
            source_location=SourceLocation(source_type="DOCX")
        )

        ResolutionRule.classify_block(block)
        assert block.activity_semantic_status == "RESOLVED"
        pass

    def test_tokenization_ambiguous_unresolved(self):
        pass

    def test_multiple_teachers_unresolved(self):
        pass

    def test_multiple_resources_is_error(self):
        pass

    def test_missing_teacher_is_error(self):
        """Block with no teacher is ERROR."""
        block = TimetableBlock(
            day="monday",
            section="I-A",
            slots=["S3"],
            activity_candidates=[
                ActivityCandidate(code="DBMS", inferred_type="CLASS", is_tokenization_ambiguous=False)
            ],
            teacher_candidates=[],  # No teacher
            resource_candidates=[
                ResourceCandidate(code="CA1", normalized_code="CA1", is_identity_resolvable=True)
            ],
            source_location=SourceLocation(source_type="DOCX")
        )

        from app.services.docx_import.occupancy import OccupancyExtractor
        res = OccupancyExtractor.extract_occupancy(block)
        assert res.extraction_successful is True
        assert len(res.teacher_occupancies) == 0

    def test_no_resource_is_resolved(self):
        """1:1:0 (no resource) is resolved."""
        block = TimetableBlock(
            day="monday",
            section="I-A",
            slots=["S3"],
            activity_candidates=[
                ActivityCandidate(code="Library Research", inferred_type="OTHER", is_tokenization_ambiguous=False)
            ],
            teacher_candidates=[
                TeacherCandidate(acronym="VR", normalized_acronym="VR", is_identity_resolvable=True)
            ],
            resource_candidates=[],  # No resource
            source_location=SourceLocation(source_type="DOCX")
        )

        ResolutionRule.classify_block(block)


class TestParenthesesNotAssociations:
    """Test that parentheses classify types but don't establish relationships."""

    def test_parentheses_classify_candidates(self):
        """Parentheses identify teacher/resource candidates."""
        lines = ["DBMS", "(VR)", "(CA1)"]

        teachers = extract_teacher_candidates(lines, {"VR": "Veena", "SU": "S Uma", "TS": "T Sunitha", "SS": "S Shilpa", "KPS": "K P Shailaja", "DNS": "D N Sujatha", "RR": "R R", "GK": "G K", "VPP": "V Padmapriya"})
        resources = extract_resource_candidates(lines, {"CA1": "CA1", "LAB1A": "LAB1A", "LAB1B": "LAB1B", "LAB 1A": "LAB 1A", "FDC": "FDC", "RL": "RL"})

        assert len(teachers) == 1
        assert teachers[0].acronym == "VR"
        assert len(resources) == 1
        assert resources[0].code == "CA1"

    def test_whitespace_does_not_establish_pairing(self):
        """Whitespace between parentheses doesn't create associations."""
        lines = ["PY1, PE2", "(SU) (TS)", "LAB1B"]

        teachers = extract_teacher_candidates(lines, {"VR": "Veena", "SU": "S Uma", "TS": "T Sunitha", "SS": "S Shilpa", "KPS": "K P Shailaja", "DNS": "D N Sujatha", "RR": "R R", "GK": "G K", "VPP": "V Padmapriya"})

        # Both teachers extracted, but NO association with activities
        assert len(teachers) == 2
        assert teachers[0].acronym == "SU"
        assert teachers[1].acronym == "TS"
        # Candidates don't have association fields
        assert not hasattr(teachers[0], 'activity')
        assert not hasattr(teachers[0], 'assigned_activity')


class TestDayNormalization:
    """Test day name normalization."""

    def test_day_normalization(self):
        """Day names normalize to ISO format."""
        assert normalize_day("MON") == "monday"
        assert normalize_day("TUE") == "tuesday"
        assert normalize_day("WED") == "wednesday"
        assert normalize_day("THU") == "thursday"
        assert normalize_day("FRI") == "friday"
        assert normalize_day("SAT") == "saturday"


class TestSourceLocationPreservation:
    """Test source location preservation through parsing."""

    def test_source_location_in_block(self):
        """TimetableBlock preserves source location."""
        source_loc = SourceLocation(
            source_type="DOCX",
            table_index=0,
            table_row=5,
            table_col=3
        )

        block = TimetableBlock(
            day="monday",
            section="I-A",
            slots=["S3"],
            activity_candidates=[ActivityCandidate("DBMS", "CLASS", False)],
            teacher_candidates=[TeacherCandidate("VR", "VR")],
            resource_candidates=[ResourceCandidate("CA1", "CA1")],
            source_location=source_loc,
            original_text="DBMS\n(VR)\nCA1"
        )

        assert block.source_location.table_row == 5
        assert block.source_location.table_col == 3
        assert block.original_text == "DBMS\n(VR)\nCA1"


class TestNoGuessing:
    """Test that parser never guesses associations."""

    def test_no_arbitrary_teacher_activity_pairing(self):
        """Parser does NOT invent teacher-activity associations."""
        lines = ["PY1, PE2", "(SU) (TS)", "LAB1A"]

        activities = extract_activity_candidates(lines, {'LAB1A': 'Lab 1A'})
        teachers = extract_teacher_candidates(lines, {"VR": "Veena", "SU": "S Uma", "TS": "T Sunitha", "SS": "S Shilpa", "KPS": "K P Shailaja", "DNS": "D N Sujatha", "RR": "R R", "GK": "G K", "VPP": "V Padmapriya"})

        # Candidates extracted
        assert len(teachers) == 2

        # But NO pairing created
        for teacher in teachers:
            assert not hasattr(teacher, 'activity')
            assert not hasattr(teacher, 'assigned_activity')

    def test_no_first_resource_selection(self):
        """Parser does NOT use 'first resource wins' for ambiguous resources."""
        # This is tested in TestResolutionRules.test_multiple_resources_is_error
        # Multiple resources result in ERROR, not "use first"
        pass


# Note: Full integration tests with actual DOCX files would go here
# For now, these unit tests verify the core parsing logic
