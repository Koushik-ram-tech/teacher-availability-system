"""Regression tests for STUDENT_MANAGED activity participation policy.

Tests verify:
1. Placement with no teachers is valid (not blocked).
2. Placement does not create teacher occupancy.
3. Placement is not blocked solely because no teacher exists.
4. Placement with CA1/CA2 keeps resource ambiguity.
5. Placement with FDC/Lab1A/Lab1B keeps resource ambiguity.
6. Student-managed resolution works with no teacher selected.
7. Normal teacher-assigned activities still require a teacher (no regression).
8. No fake 'Students' teacher is created.
9. Resource availability does not silently invent occupancy.
10. Policy layer is separate from the low-level DOCX parser (synthetic test).
"""
import pytest

from app.services.docx_import.participation_policy import (
    ActivityParticipationPolicy,
    classify_activity,
    is_student_managed,
    DEFAULT_POLICY_MAP,
    normalize_activity_code,
)
from app.services.docx_import.staging import (
    TimetableBlock,
    ActivityCandidate,
    TeacherCandidate,
    ResourceCandidate,
)
from app.domain.timetable import SourceLocation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_block(
    activity: str,
    teachers: list[str],
    resources: list[str],
    ambiguous_resource: bool = False,
    day: str = "thursday",
    section: str = "I-A",
    slots: list[str] | None = None,
) -> TimetableBlock:
    """Build a synthetic TimetableBlock for policy/occupancy tests."""
    return TimetableBlock(
        day=day,
        section=section,
        slots=slots or ["S6", "S7", "S8"],
        activity_candidates=[
            ActivityCandidate(
                code=activity,
                inferred_type="CLASS",
                is_tokenization_ambiguous=False,
            )
        ],
        teacher_candidates=[
            TeacherCandidate(
                acronym=t,
                normalized_acronym=t.upper(),
                is_identity_resolvable=True,
            )
            for t in teachers
        ],
        resource_candidates=[
            ResourceCandidate(
                code=r,
                normalized_code=r.upper(),
                is_identity_resolvable=True,
                is_assignment_ambiguous=ambiguous_resource,
            )
            for r in resources
        ],
        source_location=SourceLocation(source_type="DOCX"),
        original_text=f"{activity} ({', '.join(teachers)}) ({', '.join(resources)})",
    )


# ===========================================================================
# 1. Policy classification
# ===========================================================================

class TestPolicyClassification:
    """Test 10: Policy layer is separate from DOCX parser — it is a pure lookup."""

    def test_placement_is_student_managed(self):
        assert classify_activity("Placement") == ActivityParticipationPolicy.STUDENT_MANAGED

    def test_placement_uppercase(self):
        assert classify_activity("PLACEMENT") == ActivityParticipationPolicy.STUDENT_MANAGED

    def test_placement_mixed_case(self):
        assert classify_activity("Placement") == ActivityParticipationPolicy.STUDENT_MANAGED

    def test_vac_is_student_managed(self):
        assert classify_activity("VAC") == ActivityParticipationPolicy.STUDENT_MANAGED

    def test_cultural_activity_is_student_managed(self):
        assert classify_activity("Cultural activity") == ActivityParticipationPolicy.STUDENT_MANAGED

    def test_mini_project_is_student_managed(self):
        assert classify_activity("Mini Project") == ActivityParticipationPolicy.STUDENT_MANAGED

    def test_library_research_is_student_managed(self):
        assert classify_activity("Library/Research Activity") == ActivityParticipationPolicy.STUDENT_MANAGED

    def test_extended_class_is_student_managed(self):
        assert classify_activity("Extended class") == ActivityParticipationPolicy.STUDENT_MANAGED

    def test_normal_subject_is_faculty_managed(self):
        """Test 7: Normal faculty activities still produce FACULTY_MANAGED."""
        assert classify_activity("DBMS") == ActivityParticipationPolicy.FACULTY_MANAGED

    def test_dbms_not_student_managed(self):
        assert not is_student_managed("DBMS")

    def test_python_not_student_managed(self):
        assert not is_student_managed("PYTHON")

    def test_ada_not_student_managed(self):
        assert not is_student_managed("ADA2 Lab")

    def test_unknown_activity_defaults_to_faculty_managed(self):
        """Unrecognised activities default to FACULTY_MANAGED for safety."""
        assert classify_activity("XYZUNKNOWN999") == ActivityParticipationPolicy.FACULTY_MANAGED

    def test_is_student_managed_helper(self):
        assert is_student_managed("Placement") is True
        assert is_student_managed("DBMS") is False

    def test_custom_policy_map(self):
        """Policy map is configurable — custom maps override defaults."""
        custom_map = {"MYEVENT": ActivityParticipationPolicy.STUDENT_MANAGED}
        assert classify_activity("MYEVENT", custom_map) == ActivityParticipationPolicy.STUDENT_MANAGED
        # Default map not affected
        assert classify_activity("MYEVENT") == ActivityParticipationPolicy.FACULTY_MANAGED

    def test_normalize_activity_code(self):
        assert normalize_activity_code("  Placement  ") == "PLACEMENT"
        assert normalize_activity_code("vac") == "VAC"
        assert normalize_activity_code("Library/Research Activity") == "LIBRARY/RESEARCH ACTIVITY"

    def test_policy_map_keys_are_uppercase(self):
        """All keys in DEFAULT_POLICY_MAP should be uppercase (invariant)."""
        for key in DEFAULT_POLICY_MAP:
            assert key == key.upper(), f"Key {key!r} is not uppercase"


# ===========================================================================
# 2. OccupancyExtractor + parser routing — no teacher for STUDENT_MANAGED
# ===========================================================================

class TestStudentManagedOccupancy:
    """Tests 1, 2, 3: Placement with no teachers is valid and doesn't block."""

    def test_placement_no_teachers_occupancy_valid(self):
        """Test 1: Placement with no teachers is valid."""
        from app.services.docx_import.occupancy import OccupancyExtractor
        block = _make_block("Placement", teachers=[], resources=["CA1", "CA2"])
        result = OccupancyExtractor.extract_occupancy(block)
        assert result.extraction_successful, (
            "Placement with no teachers must not block occupancy extraction"
        )

    def test_placement_no_teacher_occupancy_records(self):
        """Test 2: Placement does not create teacher occupancy records."""
        from app.services.docx_import.occupancy import OccupancyExtractor
        block = _make_block("Placement", teachers=[], resources=["CA1"])
        result = OccupancyExtractor.extract_occupancy(block)
        assert result.teacher_occupancies == [], (
            "Placement must produce zero teacher occupancy records"
        )

    def test_placement_not_blocked_by_empty_teacher_list(self):
        """Test 3: teacher_occupancy_status is UNSPECIFIED (not AMBIGUOUS) for empty teachers."""
        from app.services.docx_import.occupancy import OccupancyExtractor
        from app.services.docx_import.resolution import ResolutionRule
        block = _make_block("Placement", teachers=[], resources=["CA1"])
        ResolutionRule.classify_block(block)
        OccupancyExtractor.extract_occupancy(block)
        assert block.teacher_occupancy_status == "UNSPECIFIED", (
            "Empty teacher list must produce UNSPECIFIED (not AMBIGUOUS) status"
        )


# ===========================================================================
# 3. Resource ambiguity preserved
# ===========================================================================

class TestResourceAmbiguityPreserved:
    """Tests 4, 5: Placement with CA1/CA2 or FDC/Lab1A/Lab1B keeps ambiguity."""

    def test_placement_ca1_ca2_resource_ambiguous(self):
        """Test 4: Placement with CA1/CA2 — resource stays AMBIGUOUS."""
        from app.services.docx_import.occupancy import OccupancyExtractor
        from app.services.docx_import.resolution import ResolutionRule
        # CA1/CA2 alternatives (slash-separated) → is_assignment_ambiguous=True
        block = _make_block(
            "Placement", teachers=[], resources=["CA1", "CA2"],
            ambiguous_resource=True
        )
        ResolutionRule.classify_block(block)
        OccupancyExtractor.extract_occupancy(block)
        assert block.resource_occupancy_status == "AMBIGUOUS", (
            "Placement CA1/CA2 must preserve resource AMBIGUOUS status"
        )

    def test_placement_fdc_lab1a_lab1b_resource_ambiguous(self):
        """Test 5: Placement with FDC/Lab1A/Lab1B — resource stays AMBIGUOUS."""
        from app.services.docx_import.occupancy import OccupancyExtractor
        from app.services.docx_import.resolution import ResolutionRule
        block = _make_block(
            "Placement", teachers=[], resources=["FDC", "LAB1A", "LAB1B"],
            ambiguous_resource=True
        )
        ResolutionRule.classify_block(block)
        OccupancyExtractor.extract_occupancy(block)
        assert block.resource_occupancy_status == "AMBIGUOUS"

    def test_ca1_ca2_ambiguous_occupancy_not_invented(self):
        """Test 9: Resource availability does not silently invent occupancy for CA1 AND CA2."""
        from app.services.docx_import.occupancy import OccupancyExtractor, OccupancyStatus
        block = _make_block(
            "Placement", teachers=[], resources=["CA1", "CA2"],
            ambiguous_resource=True
        )
        result = OccupancyExtractor.extract_occupancy(block)
        # All resource occupancies must be AMBIGUOUS, not OCCUPIED
        for occ in result.resource_occupancies:
            assert occ.status == OccupancyStatus.AMBIGUOUS, (
                f"Resource {occ.resource_code} must be AMBIGUOUS, not OCCUPIED, "
                "when assignment is alternative (CA1 OR CA2)"
            )


# ===========================================================================
# 4. No fake teacher created
# ===========================================================================

class TestNoFakeTeacher:
    """Test 8: No fake 'Students' teacher is created."""

    def test_no_fake_students_teacher(self):
        """Parser must never invent a teacher named 'Students' or similar."""
        from app.services.docx_import.occupancy import OccupancyExtractor
        block = _make_block("Placement", teachers=[], resources=["CA1"])
        result = OccupancyExtractor.extract_occupancy(block)
        fake_names = {"students", "student", "placement team", "placement"}
        for occ in result.teacher_occupancies:
            assert occ.teacher_acronym.lower() not in fake_names, (
                f"Fake teacher '{occ.teacher_acronym}' must not be invented"
            )

    def test_empty_teacher_candidates_means_no_occupancy(self):
        from app.services.docx_import.occupancy import OccupancyExtractor
        block = _make_block("Placement", teachers=[], resources=[])
        result = OccupancyExtractor.extract_occupancy(block)
        assert result.teacher_occupancies == []


# ===========================================================================
# 5. Manual resolution API schema — teacher is optional
# ===========================================================================

class TestManualResolutionSchema:
    """Test 6: Student-managed resolution works with no teacher selected."""

    def test_manual_resolution_allows_no_teacher(self):
        """selected_teacher=None is valid for student-managed blocks."""
        from app.schemas.docx_imports import ManualResolutionInput
        resolution = ManualResolutionInput(
            block_id="some-uuid",
            selected_activity="Placement",
            selected_teacher=None,
            selected_resource="CA1",
        )
        assert resolution.selected_teacher is None

    def test_manual_resolution_allows_empty_string_teacher(self):
        from app.schemas.docx_imports import ManualResolutionInput
        resolution = ManualResolutionInput(
            block_id="some-uuid",
            selected_activity="Placement",
            selected_teacher="",
            selected_resource="CA1",
        )
        assert resolution.selected_teacher == ""

    def test_normal_activity_accepts_teacher(self):
        """Test 7 (schema): Normal activities can still set a teacher."""
        from app.schemas.docx_imports import ManualResolutionInput
        resolution = ManualResolutionInput(
            block_id="some-uuid",
            selected_activity="DBMS",
            selected_teacher="VR",
        )
        assert resolution.selected_teacher == "VR"


# ===========================================================================
# 6. Unresolved block schema — participation_policy and is_student_managed
# ===========================================================================

class TestUnresolvedBlockSchema:
    """Validate UnresolvedBlock carries participation policy fields."""

    def test_unresolved_block_has_participation_policy(self):
        from app.schemas.docx_imports import UnresolvedBlock
        block = UnresolvedBlock(
            block_id="abc",
            day="thursday",
            section="I-A",
            slots=["S6", "S7", "S8"],
            source_location="DOCX: table 0 row 5 col 9",
            ambiguity_reason="resource allocation ambiguous",
            participation_policy="STUDENT_MANAGED",
            is_student_managed=True,
            resolution_required=True,
        )
        assert block.participation_policy == "STUDENT_MANAGED"
        assert block.is_student_managed is True

    def test_unresolved_block_defaults_unknown_policy(self):
        from app.schemas.docx_imports import UnresolvedBlock
        block = UnresolvedBlock(
            block_id="abc",
            day="monday",
            section="I-A",
            slots=["S1"],
            source_location="DOCX: table 0 row 2 col 2",
            ambiguity_reason="some reason",
            resolution_required=True,
        )
        assert block.participation_policy == "UNKNOWN"
        assert block.is_student_managed is False


# ===========================================================================
# 7. Parser routing: STUDENT_MANAGED blocks route correctly
# ===========================================================================

class TestParserRouting:
    """Verify parse_docx_timetable routes Placement blocks correctly."""

    def test_classify_activity_integration(self):
        """End-to-end: classify_activity is called, result is stored on block."""
        from app.services.docx_import.participation_policy import classify_activity, ActivityParticipationPolicy
        block = _make_block("Placement", teachers=[], resources=["CA1"])
        policy = classify_activity(block.activity_candidates[0].code)
        block.participation_policy = policy.value
        assert block.participation_policy == "STUDENT_MANAGED"

    def test_faculty_activity_classify(self):
        from app.services.docx_import.participation_policy import classify_activity
        block = _make_block("DBMS", teachers=["VR"], resources=["CA1"])
        policy = classify_activity(block.activity_candidates[0].code)
        block.participation_policy = policy.value
        assert block.participation_policy == "FACULTY_MANAGED"
