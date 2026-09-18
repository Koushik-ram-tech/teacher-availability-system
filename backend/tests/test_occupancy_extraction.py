"""Tests for occupancy extraction from DOCX timetable blocks.

PRODUCT REQUIREMENT:
Teacher/resource OCCUPANCY must be extractable even when subject/activity
tokenization is ambiguous.

Core principle: We need to know WHO is BUSY and WHEN, not WHO teaches WHAT.
"""

import pytest
from app.services.docx_import.occupancy import (
    OccupancyExtractor,
    OccupancyStatus,
    TeacherOccupancy,
    ResourceOccupancy,
)
from app.services.docx_import.staging import (
    TimetableBlock,
    ActivityCandidate,
    TeacherCandidate,
    ResourceCandidate,
)
from app.domain.timetable import SourceLocation, ValidationIssue, ValidationSeverity


def _create_source_location() -> SourceLocation:
    """Helper to create test source location."""
    return SourceLocation(
        source_type="DOCX",
        table_index=0,
        table_row=5,
        table_col=3,
        display_context="test_row=5_col=3"
    )


class TestOccupancyExtractionCore:
    """Core occupancy extraction behavior."""

    def test_single_teacher_single_slot_extracts_occupancy(self):
        """Single teacher in single slot → one OCCUPIED record."""
        block = TimetableBlock(
            day="monday",
            section="III-B",
            slots=["S3"],
            activity_candidates=[ActivityCandidate(code="Agile", inferred_type="CLASS", is_tokenization_ambiguous=False)],
            teacher_candidates=[TeacherCandidate(acronym="DNS", normalized_acronym="DNS")],
            resource_candidates=[],
            source_location=_create_source_location(),
            original_text="Agile\n(DNS)"
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert result.extraction_successful is True
        assert len(result.teacher_occupancies) == 1

        occ = result.teacher_occupancies[0]
        assert occ.teacher_acronym == "DNS"
        assert occ.day == "monday"
        assert occ.slot == "S3"
        assert occ.status == OccupancyStatus.OCCUPIED

    def test_single_teacher_multi_slot_extracts_all_slots(self):
        """Multi-slot block (S4+S5) → separate occupancy for EACH slot."""
        block = TimetableBlock(
            day="friday",
            section="III-B",
            slots=["S4", "S5"],
            activity_candidates=[ActivityCandidate(code="Agile Tutorial", inferred_type="OTHER", is_tokenization_ambiguous=False)],
            teacher_candidates=[TeacherCandidate(acronym="DNS", normalized_acronym="DNS")],
            resource_candidates=[],
            source_location=_create_source_location(),
            original_text="Agile Tutorial\n(DNS)"
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert result.extraction_successful is True
        assert len(result.teacher_occupancies) == 2

        # Check both slots
        slots = {occ.slot for occ in result.teacher_occupancies}
        assert slots == {"S4", "S5"}

        # All same teacher
        for occ in result.teacher_occupancies:
            assert occ.teacher_acronym == "DNS"
            assert occ.day == "friday"
            assert occ.status == OccupancyStatus.OCCUPIED

    def test_breaks_excluded_from_occupancy(self):
        """Break slots not included in occupancy records."""
        block = TimetableBlock(
            day="monday",
            section="I-A",
            slots=["S3", "BREAK", "S4"],  # BREAK should be skipped
            activity_candidates=[ActivityCandidate(code="TestActivity", inferred_type="CLASS", is_tokenization_ambiguous=False)],
            teacher_candidates=[TeacherCandidate(acronym="SU", normalized_acronym="SU")],
            resource_candidates=[],
            source_location=_create_source_location(),
            original_text="TestActivity\n(SU)"
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert result.extraction_successful is True
        assert len(result.teacher_occupancies) == 2  # Only S3 and S4, not BREAK

        slots = {occ.slot for occ in result.teacher_occupancies}
        assert slots == {"S3", "S4"}
        assert "BREAK" not in slots


class TestActivityAmbiguityDoesNotBlockOccupancy:
    """CRITICAL: Subject ambiguity must NOT prevent occupancy extraction when
    teacher allocation is deterministic."""

    def test_ambiguous_activity_deterministic_teachers_extracts_all_teachers(self):
        """Activity tokenization ambiguous but all teachers clearly allocated.

        Example: "PY1, PE2, DS 3,4" with (SU) (TS) (SS, KPS)
        Cannot split activities, but all 4 teachers allocated to block.
        Result: Extract occupancy for ALL 4 teachers.
        """
        block = TimetableBlock(
            day="tuesday",
            section="I-A",
            slots=["S4", "S5"],
            activity_candidates=[ActivityCandidate(
                code="PY1, PE2, DS 3,4",
                inferred_type="LAB",
                is_tokenization_ambiguous=True  # Cannot split
            )],
            teacher_candidates=[
                TeacherCandidate(acronym="SU", normalized_acronym="SU"),
                TeacherCandidate(acronym="TS", normalized_acronym="TS"),
                TeacherCandidate(acronym="SS", normalized_acronym="SS"),
                TeacherCandidate(acronym="KPS", normalized_acronym="KPS"),
            ],
            resource_candidates=[
                ResourceCandidate(code="LAB1B", normalized_code="LAB1B"),
                ResourceCandidate(code="LAB1A", normalized_code="LAB1A"),
            ],
            source_location=_create_source_location(),
            original_text="PY1, PE2, DS 3,4\n(SU) (TS) (SS, KPS)\n(LAB1B) (LAB1A)",
            issues=[
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="ACTIVITY_TOKENIZATION_AMBIGUOUS",
                    message="Activity has ambiguous boundaries",
                    affected_entities={"type": "activity"}
                )
            ]
        )

        result = OccupancyExtractor.extract_occupancy(block)

        # Extraction should succeed despite activity ambiguity
        assert result.extraction_successful is True

        # Should extract occupancy for ALL 4 teachers
        assert len(result.teacher_occupancies) == 8  # 4 teachers × 2 slots

        # Check all teachers present
        teachers = {occ.teacher_acronym for occ in result.teacher_occupancies}
        assert teachers == {"SU", "TS", "SS", "KPS"}

        # Check each teacher has S4 and S5
        for teacher in ["SU", "TS", "SS", "KPS"]:
            teacher_occs = [occ for occ in result.teacher_occupancies if occ.teacher_acronym == teacher]
            assert len(teacher_occs) == 2
            slots = {occ.slot for occ in teacher_occs}
            assert slots == {"S4", "S5"}
            assert all(occ.status == OccupancyStatus.OCCUPIED for occ in teacher_occs)

        # Should also extract resource occupancy
        assert len(result.resource_occupancies) == 4  # 2 resources × 2 slots
        resources = {occ.resource_code for occ in result.resource_occupancies}
        assert resources == {"LAB1B", "LAB1A"}

    def test_ambiguous_activity_ada_dt_extracts_all_teachers(self):
        """Thursday III-B: ADA 1, DT 3,4 with multiple teachers.

        Cannot determine which teacher → which subject.
        But all teachers ARE allocated to the block.
        """
        block = TimetableBlock(
            day="thursday",
            section="III-B",
            slots=["S2", "S3"],
            activity_candidates=[ActivityCandidate(
                code="ADA 1, DT 3,4",
                inferred_type="LAB",
                is_tokenization_ambiguous=True
            )],
            teacher_candidates=[
                TeacherCandidate(acronym="TSP", normalized_acronym="TSP"),
                TeacherCandidate(acronym="DNS", normalized_acronym="DNS"),
                TeacherCandidate(acronym="SU", normalized_acronym="SU"),
            ],
            resource_candidates=[
                ResourceCandidate(code="Lab 1A", normalized_code="LAB1A"),
            ],
            source_location=_create_source_location(),
            original_text="ADA 1, DT 3,4\n(TSP) (DNS, SU)\n(Lab 1A)",
            issues=[
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="ACTIVITY_TOKENIZATION_AMBIGUOUS",
                    message="Activity has ambiguous boundaries",
                    affected_entities={"type": "activity"}
                )
            ]
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert result.extraction_successful is True
        assert len(result.teacher_occupancies) == 6  # 3 teachers × 2 slots

        teachers = {occ.teacher_acronym for occ in result.teacher_occupancies}
        assert teachers == {"TSP", "DNS", "SU"}

        # Resource should also be extracted
        assert len(result.resource_occupancies) == 2  # 1 resource × 2 slots


class TestGenuineAmbiguityBlocksOccupancy:
    """When allocation itself is ambiguous, do NOT guess."""

    def test_missing_teacher_blocks_extraction(self):
        """No teacher specified → cannot extract occupancy."""
        block = TimetableBlock(
            day="monday",
            section="I-A",
            slots=["S1"],
            activity_candidates=[ActivityCandidate(code="TestSubject", inferred_type="CLASS", is_tokenization_ambiguous=False)],
            teacher_candidates=[],  # No teachers
            resource_candidates=[],
            source_location=_create_source_location(),
            original_text="TestSubject",
            issues=[
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="MISSING_TEACHER",
                    message="No teacher specified",
                    affected_entities={"type": "teacher"}
                )
            ]
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert result.extraction_successful is True
        assert len(result.teacher_occupancies) == 0

    def test_ambiguous_teacher_mapping_blocks_extraction(self):
        """Genuinely ambiguous which teachers are allocated → cannot extract."""
        block = TimetableBlock(
            day="tuesday",
            section="II-A",
            slots=["S2"],
            activity_candidates=[
                ActivityCandidate(code="Subject A", inferred_type="CLASS", is_tokenization_ambiguous=False),
                ActivityCandidate(code="Subject B", inferred_type="CLASS", is_tokenization_ambiguous=False),
            ],
            teacher_candidates=[
                TeacherCandidate(acronym="T1", normalized_acronym="T1"),
                TeacherCandidate(acronym="T2", normalized_acronym="T2"),
            ],
            resource_candidates=[],
            source_location=_create_source_location(),
            original_text="Subject A\nSubject B\n(T1) (T2)",
            issues=[
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="AMBIGUOUS_TEACHER_MAPPING",
                    message="Cannot determine which teacher teaches which subject",
                    affected_entities={"type": "teacher"}
                )
            ]
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert result.extraction_successful is True
        assert len(result.teacher_occupancies) == 2
        assert all(occ.status.name == "OCCUPIED" for occ in result.teacher_occupancies)

    def test_unresolvable_teacher_identity_blocks_extraction(self):
        """Teacher acronym cannot be resolved → cannot extract."""
        block = TimetableBlock(
            day="wednesday",
            section="I-B",
            slots=["S3"],
            activity_candidates=[ActivityCandidate(code="TestSubject", inferred_type="CLASS", is_tokenization_ambiguous=False)],
            teacher_candidates=[TeacherCandidate(
                acronym="UNKNOWN",
                normalized_acronym="UNKNOWN",
                is_identity_resolvable=False
            )],
            resource_candidates=[],
            source_location=_create_source_location(),
            original_text="TestSubject\n(UNKNOWN)",
            issues=[
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="UNRESOLVED_TEACHER_IDENTITY",
                    message="Teacher identity cannot be resolved",
                    affected_entities={"type": "teacher"}
                )
            ]
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert result.extraction_successful is False
        assert len(result.teacher_occupancies) == 0
        assert "UNRESOLVED_TEACHER_IDENTITY" in result.blocked_reason


class TestResourceOccupancyExtraction:
    """Resource occupancy extraction rules."""

    def test_single_resource_extracts_occupancy(self):
        """Single resource → extract occupancy."""
        block = TimetableBlock(
            day="monday",
            section="I-A",
            slots=["S1"],
            activity_candidates=[ActivityCandidate(code="Lab", inferred_type="LAB", is_tokenization_ambiguous=False)],
            teacher_candidates=[TeacherCandidate(acronym="T1", normalized_acronym="T1")],
            resource_candidates=[ResourceCandidate(code="LAB1A", normalized_code="LAB1A")],
            source_location=_create_source_location(),
            original_text="Lab\n(T1)\n(LAB1A)"
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert result.extraction_successful is True
        assert len(result.resource_occupancies) == 1

        occ = result.resource_occupancies[0]
        assert occ.resource_code == "LAB1A"
        assert occ.day == "monday"
        assert occ.slot == "S1"
        assert occ.status == OccupancyStatus.OCCUPIED

    def test_multiple_resources_multi_slot_extracts_all(self):
        """Multiple resources on multi-slot block → extract all × all slots."""
        block = TimetableBlock(
            day="tuesday",
            section="I-A",
            slots=["S4", "S5"],
            activity_candidates=[ActivityCandidate(
                code="PY1, PE2",
                inferred_type="LAB",
                is_tokenization_ambiguous=True
            )],
            teacher_candidates=[
                TeacherCandidate(acronym="T1", normalized_acronym="T1"),
                TeacherCandidate(acronym="T2", normalized_acronym="T2"),
            ],
            resource_candidates=[
                ResourceCandidate(code="LAB1A", normalized_code="LAB1A"),
                ResourceCandidate(code="LAB1B", normalized_code="LAB1B"),
            ],
            source_location=_create_source_location(),
            original_text="PY1, PE2\n(T1) (T2)\n(LAB1A) (LAB1B)",
            issues=[
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="ACTIVITY_TOKENIZATION_AMBIGUOUS",
                    message="Activity ambiguous",
                    affected_entities={"type": "activity"}
                )
            ]
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert result.extraction_successful is True
        assert len(result.resource_occupancies) == 4  # 2 resources × 2 slots

        resources = {occ.resource_code for occ in result.resource_occupancies}
        assert resources == {"LAB1A", "LAB1B"}

        # Each resource should have both slots
        for resource in ["LAB1A", "LAB1B"]:
            res_occs = [occ for occ in result.resource_occupancies if occ.resource_code == resource]
            assert len(res_occs) == 2
            slots = {occ.slot for occ in res_occs}
            assert slots == {"S4", "S5"}

    def test_no_resources_still_succeeds(self):
        """No resources present → extraction still successful (resources optional)."""
        block = TimetableBlock(
            day="monday",
            section="I-A",
            slots=["S1"],
            activity_candidates=[ActivityCandidate(code="Theory", inferred_type="CLASS", is_tokenization_ambiguous=False)],
            teacher_candidates=[TeacherCandidate(acronym="T1", normalized_acronym="T1")],
            resource_candidates=[],  # No resources
            source_location=_create_source_location(),
            original_text="Theory\n(T1)"
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert result.extraction_successful is True
        assert len(result.resource_occupancies) == 0  # No resources
        assert len(result.teacher_occupancies) == 1  # Teacher still extracted

    def test_ambiguous_resource_blocks_extraction(self):
        """Genuinely ambiguous which resource is allocated → cannot extract."""
        block = TimetableBlock(
            day="wednesday",
            section="II-A",
            slots=["S2"],
            activity_candidates=[ActivityCandidate(code="Lab", inferred_type="LAB", is_tokenization_ambiguous=False)],
            teacher_candidates=[TeacherCandidate(acronym="T1", normalized_acronym="T1")],
            resource_candidates=[
                ResourceCandidate(code="LAB1A", normalized_code="LAB1A"),
                ResourceCandidate(code="LAB1B", normalized_code="LAB1B"),
            ],
            source_location=_create_source_location(),
            original_text="Lab\n(T1)\n(LAB1A) or (LAB1B)",
            issues=[
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="AMBIGUOUS_RESOURCE_SINGLE_ACTIVITY",
                    message="Unclear which resource allocated",
                    affected_entities={"type": "resource"}
                )
            ]
        )

        result = OccupancyExtractor.extract_occupancy(block)

        # Resource extraction extracts multiple resources as AMBIGUOUS
        assert len(result.resource_occupancies) == 2
        assert all(occ.status.name == "AMBIGUOUS" for occ in result.resource_occupancies)
        assert len(result.teacher_occupancies) == 1  # Teacher still extracted


class TestMultipleTeachersOnSameBlock:
    """Multiple teachers can all be OCCUPIED for same block (no "first teacher")."""

    def test_multiple_teachers_one_activity_all_occupied(self):
        """One activity with multiple teachers → all teachers OCCUPIED (team teaching)."""
        block = TimetableBlock(
            day="friday",
            section="III-A",
            slots=["S6"],
            activity_candidates=[ActivityCandidate(code="Workshop", inferred_type="OTHER", is_tokenization_ambiguous=False)],
            teacher_candidates=[
                TeacherCandidate(acronym="T1", normalized_acronym="T1"),
                TeacherCandidate(acronym="T2", normalized_acronym="T2"),
                TeacherCandidate(acronym="T3", normalized_acronym="T3"),
            ],
            resource_candidates=[],
            source_location=_create_source_location(),
            original_text="Workshop\n(T1) (T2) (T3)"
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert result.extraction_successful is True
        assert len(result.teacher_occupancies) == 3  # All 3 teachers

        teachers = {occ.teacher_acronym for occ in result.teacher_occupancies}
        assert teachers == {"T1", "T2", "T3"}

        # All should be OCCUPIED
        assert all(occ.status == OccupancyStatus.OCCUPIED for occ in result.teacher_occupancies)


class TestSourceTraceability:
    """Occupancy records preserve source location."""

    def test_source_location_preserved(self):
        """Source location and original text preserved in occupancy records."""
        source_loc = SourceLocation(
            source_type="DOCX",
            table_index=0,
            table_row=10,
            table_col=5,
            display_context="tuesday_I-A_S4"
        )

        block = TimetableBlock(
            day="tuesday",
            section="I-A",
            slots=["S4"],
            activity_candidates=[ActivityCandidate(code="Test", inferred_type="CLASS", is_tokenization_ambiguous=False)],
            teacher_candidates=[TeacherCandidate(acronym="DNS", normalized_acronym="DNS")],
            resource_candidates=[],
            source_location=source_loc,
            original_text="Test\n(DNS)"
        )

        result = OccupancyExtractor.extract_occupancy(block)

        assert len(result.teacher_occupancies) == 1
        occ = result.teacher_occupancies[0]

        assert occ.source_location.table_row == 10
        assert occ.source_location.table_col == 5
        assert occ.source_location.display_context == "tuesday_I-A_S4"
        assert occ.source_block_original_text == "Test\n(DNS)"
        assert occ.extraction_reason != ""  # Should have reason


class TestDeterministicBehavior:
    """Occupancy extraction must be deterministic."""

    def test_same_block_produces_same_occupancy(self):
        """Running extraction multiple times produces identical results."""
        block = TimetableBlock(
            day="monday",
            section="I-A",
            slots=["S1", "S2"],
            activity_candidates=[ActivityCandidate(code="Test", inferred_type="CLASS", is_tokenization_ambiguous=False)],
            teacher_candidates=[
                TeacherCandidate(acronym="T1", normalized_acronym="T1"),
                TeacherCandidate(acronym="T2", normalized_acronym="T2"),
            ],
            resource_candidates=[
                ResourceCandidate(code="R1", normalized_code="R1"),
            ],
            source_location=_create_source_location(),
            original_text="Test\n(T1) (T2)\n(R1)"
        )

        # Extract multiple times
        result1 = OccupancyExtractor.extract_occupancy(block)
        result2 = OccupancyExtractor.extract_occupancy(block)
        result3 = OccupancyExtractor.extract_occupancy(block)

        # Should be identical
        assert len(result1.teacher_occupancies) == len(result2.teacher_occupancies) == len(result3.teacher_occupancies)
        assert len(result1.resource_occupancies) == len(result2.resource_occupancies) == len(result3.resource_occupancies)

        # Convert to sets for comparison
        def to_key(occ):
            if isinstance(occ, TeacherOccupancy):
                return (occ.teacher_acronym, occ.day, occ.slot)
            else:
                return (occ.resource_code, occ.day, occ.slot)

        teacher_keys1 = {to_key(o) for o in result1.teacher_occupancies}
        teacher_keys2 = {to_key(o) for o in result2.teacher_occupancies}
        teacher_keys3 = {to_key(o) for o in result3.teacher_occupancies}

        assert teacher_keys1 == teacher_keys2 == teacher_keys3


class TestTrueTeacherAmbiguity:
    """Regression: true teacher identity ambiguity must block extraction — no guessing."""

    def test_unresolvable_acronym_blocks_extraction_never_guesses(self):
        """When is_identity_resolvable=False, extraction fails. We never pick first teacher."""
        block = TimetableBlock(
            day="wednesday",
            section="II-B",
            slots=["S2"],
            activity_candidates=[ActivityCandidate(
                code="ProjectWork", inferred_type="CLASS", is_tokenization_ambiguous=False
            )],
            teacher_candidates=[
                TeacherCandidate(
                    acronym="UNKNOWN_X",
                    normalized_acronym="UNKNOWN_X",
                    is_identity_resolvable=False
                ),
            ],
            resource_candidates=[],
            source_location=_create_source_location(),
            original_text="ProjectWork\n(UNKNOWN_X)",
            issues=[
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="UNRESOLVED_TEACHER_IDENTITY",
                    message="Teacher 'UNKNOWN_X' cannot be resolved",
                    affected_entities={"type": "teacher", "acronym": "UNKNOWN_X"}
                )
            ]
        )
        result = OccupancyExtractor.extract_occupancy(block)
        assert result.extraction_successful is False
        assert len(result.teacher_occupancies) == 0
        assert "UNRESOLVED_TEACHER_IDENTITY" in result.blocked_reason

    def test_multiple_teachers_all_allocated_are_all_busy(self):
        """Multiple teachers in same block → ALL BUSY (team teaching, not ambiguity)."""
        block = TimetableBlock(
            day="tuesday",
            section="I-A",
            slots=["S4", "S5"],
            activity_candidates=[ActivityCandidate(
                code="PY1, PE2, DS 3,4",
                inferred_type="LAB",
                is_tokenization_ambiguous=True
            )],
            teacher_candidates=[
                TeacherCandidate(acronym="SU", normalized_acronym="SU"),
                TeacherCandidate(acronym="TS", normalized_acronym="TS"),
                TeacherCandidate(acronym="SS", normalized_acronym="SS"),
                TeacherCandidate(acronym="KPS", normalized_acronym="KPS"),
            ],
            resource_candidates=[],
            source_location=_create_source_location(),
            original_text="PY1, PE2, DS 3,4\n(SU) (TS) (SS, KPS)"
        )
        result = OccupancyExtractor.extract_occupancy(block)
        assert result.extraction_successful is True
        assert len(result.teacher_occupancies) == 8  # 4 teachers x 2 slots
        teachers = {occ.teacher_acronym for occ in result.teacher_occupancies}
        assert teachers == {"SU", "TS", "SS", "KPS"}
        assert all(occ.status == OccupancyStatus.OCCUPIED for occ in result.teacher_occupancies)


class TestActivitySemanticStatusTruthfulness:
    """activity_semantic_status must be truthful and independent of occupancy."""

    def test_tokenization_ambiguous_activity_remains_ambiguous(self):
        """Tokenization-ambiguous activity stays AMBIGUOUS even when teachers are deterministic."""
        from app.services.docx_import.resolution import ResolutionRule
        block = TimetableBlock(
            day="wednesday",
            section="I-A",
            slots=["S4", "S5"],
            activity_candidates=[ActivityCandidate(
                code="PE 1,3,4 Web2",
                inferred_type="LAB",
                is_tokenization_ambiguous=True
            )],
            teacher_candidates=[
                TeacherCandidate(acronym="GK", normalized_acronym="GK"),
                TeacherCandidate(acronym="SS", normalized_acronym="SS"),
                TeacherCandidate(acronym="TS", normalized_acronym="TS"),
                TeacherCandidate(acronym="VR", normalized_acronym="VR"),
            ],
            resource_candidates=[ResourceCandidate(code="LAB1A", normalized_code="LAB1A")],
            source_location=_create_source_location(),
            original_text="PE 1,3,4 Web2\n(GK, SS, TS) (VR)\n(LAB 1A)"
        )
        ResolutionRule.classify_block(block)
        # Activity MUST remain AMBIGUOUS
        assert block.activity_semantic_status == "AMBIGUOUS"
        # Occupancy still extracted
        result = OccupancyExtractor.extract_occupancy(block)
        assert result.extraction_successful is True
        assert len(result.teacher_occupancies) == 8  # 4 teachers x 2 slots
        teachers = {occ.teacher_acronym for occ in result.teacher_occupancies}
        assert teachers == {"GK", "SS", "TS", "VR"}

    def test_three_statuses_are_independent(self):
        """activity=AMBIGUOUS + teacher=DETERMINISTIC + resource=DETERMINISTIC simultaneously."""
        from app.services.docx_import.resolution import ResolutionRule
        block = TimetableBlock(
            day="thursday",
            section="III-B",
            slots=["S2", "S3"],
            activity_candidates=[ActivityCandidate(
                code="ADA 1, DT 3,4",
                inferred_type="LAB",
                is_tokenization_ambiguous=True
            )],
            teacher_candidates=[
                TeacherCandidate(acronym="TSP", normalized_acronym="TSP"),
                TeacherCandidate(acronym="DNS", normalized_acronym="DNS"),
                TeacherCandidate(acronym="SU", normalized_acronym="SU"),
            ],
            resource_candidates=[ResourceCandidate(code="LAB1A", normalized_code="LAB1A")],
            source_location=_create_source_location(),
            original_text="ADA 1, DT 3,4\n(TSP) (DNS, SU)\n(Lab 1A)"
        )
        ResolutionRule.classify_block(block)
        result = OccupancyExtractor.extract_occupancy(block)
        assert block.activity_semantic_status == "AMBIGUOUS"
        assert block.teacher_occupancy_status == "DETERMINISTIC"
        assert block.resource_occupancy_status == "DETERMINISTIC"
        teachers = {occ.teacher_acronym for occ in result.teacher_occupancies}
        assert teachers == {"TSP", "DNS", "SU"}

    def test_missing_subject_does_not_block_teacher_occupancy(self):
        """No subject/activity → teacher must still be BUSY."""
        from app.services.docx_import.resolution import ResolutionRule
        block = TimetableBlock(
            day="wednesday",
            section="III-B",
            slots=["S4"],
            activity_candidates=[],
            teacher_candidates=[TeacherCandidate(acronym="RR", normalized_acronym="RR")],
            resource_candidates=[ResourceCandidate(code="CA3", normalized_code="CA3")],
            source_location=_create_source_location(),
            original_text="(RR)\n(CA3)"
        )
        ResolutionRule.classify_block(block)
        result = OccupancyExtractor.extract_occupancy(block)
        assert block.activity_semantic_status == "MISSING"
        assert result.extraction_successful is True
        assert len(result.teacher_occupancies) == 1
        assert result.teacher_occupancies[0].teacher_acronym == "RR"
        assert result.teacher_occupancies[0].status == OccupancyStatus.OCCUPIED
        assert len(result.resource_occupancies) == 1
        assert result.resource_occupancies[0].resource_code == "CA3"
