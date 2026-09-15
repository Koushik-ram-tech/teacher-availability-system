"""Tests for manual resolution logic (staging-only, no database)."""

import uuid

from app.domain.timetable import SourceLocation, ValidationSeverity
from app.services.docx_import.staging import (
    DOCXImportPreview,
    UnresolvedTimetableBlock,
    ManualResolutionMapping,
)
from app.services.docx_import.manual_resolution import (
    apply_manual_resolution,
    apply_multiple_resolutions,
    exclude_candidate,
    validate_resolution_completeness,
)


class TestManualResolution:
    """Test manual resolution of unresolved blocks."""

    def test_apply_single_resolution(self):
        """Apply manual resolution to one unresolved block."""
        # Create preview with unresolved block
        preview = DOCXImportPreview(
            import_session_id=str(uuid.uuid4()),
            academic_year="2026-Odd",
            department="Computer Applications",
            source_file="test.docx",
            unresolved_count=1
        )

        block_id = str(uuid.uuid4())
        unresolved = UnresolvedTimetableBlock(
            temp_id=block_id,
            day="tuesday",
            section="I-A",
            slots=["S4", "S5"],
            activity_candidates=[
                {"code": "PY1", "inferred_type": "LAB", "is_tokenization_ambiguous": False},
                {"code": "PE2", "inferred_type": "LAB", "is_tokenization_ambiguous": False},
            ],
            teacher_candidates=[
                {"acronym": "SU", "normalized_acronym": "SU", "is_identity_resolvable": True},
                {"acronym": "TS", "normalized_acronym": "TS", "is_identity_resolvable": True},
            ],
            resource_candidates=[
                {"code": "LAB1A", "normalized_code": "LAB1A", "is_identity_resolvable": True},
            ],
            ambiguity_reason="Multiple activities and teachers, cannot determine pairing",
            original_text="PY1\nPE2\n(SU, TS)\nLAB1A",
            source_location={
                "source_type": "DOCX",
                "source_identifier": "test.docx",
                "table_index": 0,
                "table_row": 5,
                "table_col": 6,
                "original_text": "PY1\nPE2\n(SU, TS)\nLAB1A"
            },
            issues=[]
        )

        preview.unresolved_blocks.append(unresolved)

        # Apply resolution: PY1 + SU + LAB1A
        mapping = ManualResolutionMapping(
            source_block_temp_id=block_id,
            selected_activity="PY1",
            selected_teacher="SU",
            selected_resource="LAB1A",
            resolved_by="test_user",
            resolution_notes="First activity pair"
        )

        success, error = apply_manual_resolution(preview, mapping)

        assert success
        assert error is None
        assert len(preview.resolved_blocks) == 1
        assert len(preview.unresolved_blocks) == 1  # Block still present!
        assert preview.manually_resolved_count == 1

        resolved = preview.resolved_blocks[0]
        assert resolved.subject_or_activity == "PY1"
        assert resolved.teacher_acronym == "SU"
        assert resolved.resource_code == "LAB1A"
        assert resolved.manually_resolved is True
        assert resolved.day == "tuesday"
        assert resolved.section == "I-A"
        assert resolved.slots == ["S4", "S5"]

        # Can apply second resolution to same block
        mapping2 = ManualResolutionMapping(
            source_block_temp_id=block_id,
            selected_activity="PE2",
            selected_teacher="TS",
            selected_resource="LAB1A",
            resolved_by="test_user",
            resolution_notes="Second activity pair"
        )

        success2, error2 = apply_manual_resolution(preview, mapping2)
        assert success2
        assert len(preview.resolved_blocks) == 2
        assert len(preview.unresolved_blocks) == 1  # Still present!

        # Finalize block
        from app.services.docx_import.manual_resolution import finalize_block_resolution
        success_fin, error_fin = finalize_block_resolution(preview, block_id)
        assert success_fin
        assert len(preview.unresolved_blocks) == 0  # Now removed

    def test_multiple_resolutions_from_one_block(self):
        """One unresolved block can produce multiple resolved activities."""
        preview = DOCXImportPreview(
            import_session_id=str(uuid.uuid4()),
            academic_year="2026-Odd",
            department="Computer Applications",
            source_file="test.docx",
            unresolved_count=1
        )

        block_id = str(uuid.uuid4())
        unresolved = UnresolvedTimetableBlock(
            temp_id=block_id,
            day="tuesday",
            section="I-A",
            slots=["S4", "S5"],
            activity_candidates=[
                {"code": "PY1", "inferred_type": "LAB", "is_tokenization_ambiguous": False},
                {"code": "PE2", "inferred_type": "LAB", "is_tokenization_ambiguous": False},
                {"code": "DS3", "inferred_type": "LAB", "is_tokenization_ambiguous": False},
                {"code": "ADA4", "inferred_type": "LAB", "is_tokenization_ambiguous": False},
            ],
            teacher_candidates=[
                {"acronym": "SU", "normalized_acronym": "SU", "is_identity_resolvable": True},
                {"acronym": "TS", "normalized_acronym": "TS", "is_identity_resolvable": True},
                {"acronym": "VPP", "normalized_acronym": "VPP", "is_identity_resolvable": True},
                {"acronym": "VR", "normalized_acronym": "VR", "is_identity_resolvable": True},
            ],
            resource_candidates=[],
            ambiguity_reason="4 activities, 4 teachers, cannot determine associations",
            original_text="PY1 PE2 DS3 ADA4\n(SU, TS, VPP, VR)",
            source_location={
                "source_type": "DOCX",
                "source_identifier": "test.docx",
                "table_index": 0,
                "table_row": 5,
                "table_col": 6,
                "original_text": "PY1 PE2 DS3 ADA4\n(SU, TS, VPP, VR)"
            },
            issues=[]
        )

        preview.unresolved_blocks.append(unresolved)

        # User explicitly maps each pairing
        mappings = [
            ManualResolutionMapping(
                source_block_temp_id=block_id,
                selected_activity="PY1",
                selected_teacher="SU",
                selected_resource=None,
                resolved_by="test_user"
            ),
            ManualResolutionMapping(
                source_block_temp_id=block_id,
                selected_activity="PE2",
                selected_teacher="TS",
                selected_resource=None,
                resolved_by="test_user"
            ),
            ManualResolutionMapping(
                source_block_temp_id=block_id,
                selected_activity="DS3",
                selected_teacher="VPP",
                selected_resource=None,
                resolved_by="test_user"
            ),
            ManualResolutionMapping(
                source_block_temp_id=block_id,
                selected_activity="ADA4",
                selected_teacher="VR",
                selected_resource=None,
                resolved_by="test_user"
            ),
        ]

        # Apply all mappings - block should remain until finalized
        success_count, errors = apply_multiple_resolutions(preview, mappings)

        assert success_count == 4
        assert len(errors) == 0
        assert len(preview.resolved_blocks) == 4
        assert len(preview.unresolved_blocks) == 1  # Block still present!
        assert preview.manually_resolved_count == 4

        # Verify all activities created
        activities = {r.subject_or_activity for r in preview.resolved_blocks}
        assert activities == {"PY1", "PE2", "DS3", "ADA4"}

        # Verify all have same source location
        for resolved in preview.resolved_blocks:
            assert resolved.day == "tuesday"
            assert resolved.section == "I-A"
            assert resolved.slots == ["S4", "S5"]
            assert resolved.source_location.table_row == 5
            assert resolved.source_location.table_col == 6

        # Finalize the block
        from app.services.docx_import.manual_resolution import finalize_block_resolution
        success, error = finalize_block_resolution(preview, block_id)

        assert success
        assert error is None
        assert len(preview.unresolved_blocks) == 0  # Now removed
        assert preview.unresolved_count == 0

    def test_source_location_preserved(self):
        """Source location is preserved through manual resolution."""
        preview = DOCXImportPreview(
            import_session_id=str(uuid.uuid4()),
            academic_year="2026-Odd",
            department="Computer Applications",
            source_file="test.docx"
        )

        block_id = str(uuid.uuid4())
        unresolved = UnresolvedTimetableBlock(
            temp_id=block_id,
            day="monday",
            section="I-A",
            slots=["S1"],
            activity_candidates=[{"code": "DBMS", "inferred_type": "CLASS", "is_tokenization_ambiguous": False}],
            teacher_candidates=[{"acronym": "RAJ", "normalized_acronym": "RAJ", "is_identity_resolvable": True}],
            resource_candidates=[],
            ambiguity_reason="Test",
            original_text="DBMS\n(RAJ)",
            source_location={
                "source_type": "DOCX",
                "source_identifier": "test.docx",
                "table_index": 0,
                "table_row": 10,
                "table_col": 3,
                "original_text": "DBMS\n(RAJ)"
            },
            issues=[]
        )

        preview.unresolved_blocks.append(unresolved)

        mapping = ManualResolutionMapping(
            source_block_temp_id=block_id,
            selected_activity="DBMS",
            selected_teacher="RAJ",
            selected_resource=None,
            resolved_by="test_user"
        )

        apply_manual_resolution(preview, mapping)

        resolved = preview.resolved_blocks[0]
        assert resolved.source_location.source_type == "DOCX"
        assert resolved.source_location.table_index == 0
        assert resolved.source_location.table_row == 10
        assert resolved.source_location.table_col == 3

    def test_invalid_activity_selection(self):
        """Cannot select activity not in candidates."""
        preview = DOCXImportPreview(
            import_session_id=str(uuid.uuid4()),
            academic_year="2026-Odd",
            department="Computer Applications",
            source_file="test.docx"
        )

        block_id = str(uuid.uuid4())
        unresolved = UnresolvedTimetableBlock(
            temp_id=block_id,
            day="monday",
            section="I-A",
            slots=["S1"],
            activity_candidates=[{"code": "DBMS", "inferred_type": "CLASS", "is_tokenization_ambiguous": False}],
            teacher_candidates=[{"acronym": "RAJ", "normalized_acronym": "RAJ", "is_identity_resolvable": True}],
            resource_candidates=[],
            ambiguity_reason="Test",
            original_text="DBMS\n(RAJ)",
            source_location={
                "source_type": "DOCX",
                "source_identifier": "test.docx",
                "table_index": 0,
                "table_row": 10,
                "table_col": 3,
                "original_text": "DBMS\n(RAJ)"
            },
            issues=[]
        )

        preview.unresolved_blocks.append(unresolved)

        # Try to select activity not in candidates
        mapping = ManualResolutionMapping(
            source_block_temp_id=block_id,
            selected_activity="INVENTED_ACTIVITY",  # Not in candidates
            selected_teacher="RAJ",
            selected_resource=None,
            resolved_by="test_user"
        )

        success, error = apply_manual_resolution(preview, mapping)

        assert not success
        assert "not in block candidates" in error
        assert len(preview.resolved_blocks) == 0
        assert len(preview.unresolved_blocks) == 1  # Still unresolved

    def test_exclude_candidate(self):
        """Candidate can be marked as excluded."""
        preview = DOCXImportPreview(
            import_session_id=str(uuid.uuid4()),
            academic_year="2026-Odd",
            department="Computer Applications",
            source_file="test.docx"
        )

        block_id = str(uuid.uuid4())
        unresolved = UnresolvedTimetableBlock(
            temp_id=block_id,
            day="tuesday",
            section="I-A",
            slots=["S4"],
            activity_candidates=[
                {"code": "PY1", "inferred_type": "LAB", "is_tokenization_ambiguous": False},
                {"code": "NOISE", "inferred_type": "OTHER", "is_tokenization_ambiguous": False},
            ],
            teacher_candidates=[{"acronym": "SU", "normalized_acronym": "SU", "is_identity_resolvable": True}],
            resource_candidates=[],
            ambiguity_reason="Multiple activities",
            original_text="PY1 NOISE\n(SU)",
            source_location={
                "source_type": "DOCX",
                "source_identifier": "test.docx",
                "table_index": 0,
                "table_row": 5,
                "table_col": 6,
                "original_text": "PY1 NOISE\n(SU)"
            },
            issues=[]
        )

        preview.unresolved_blocks.append(unresolved)

        # Exclude the "NOISE" activity
        success, error = exclude_candidate(
            preview,
            block_id,
            "activity",
            "NOISE",
            "Not a real activity, OCR artifact"
        )

        assert success
        assert error is None
        assert len(preview.excluded_candidates) == 1

        excluded = preview.excluded_candidates[0]
        assert excluded.candidate_type == "activity"
        assert excluded.candidate_code == "NOISE"
        assert excluded.exclusion_reason == "Not a real activity, OCR artifact"

    def test_unresolved_blocks_cannot_convert(self):
        """Unresolved blocks prevent conversion to canonical."""
        preview = DOCXImportPreview(
            import_session_id=str(uuid.uuid4()),
            academic_year="2026-Odd",
            department="Computer Applications",
            source_file="test.docx"
        )

        block_id = str(uuid.uuid4())
        unresolved = UnresolvedTimetableBlock(
            temp_id=block_id,
            day="tuesday",
            section="I-A",
            slots=["S4"],
            activity_candidates=[{"code": "PY1", "inferred_type": "LAB", "is_tokenization_ambiguous": False}],
            teacher_candidates=[{"acronym": "SU", "normalized_acronym": "SU", "is_identity_resolvable": True}],
            resource_candidates=[],
            ambiguity_reason="Test",
            original_text="PY1\n(SU)",
            source_location={
                "source_type": "DOCX",
                "source_identifier": "test.docx",
                "table_index": 0,
                "table_row": 5,
                "table_col": 6,
                "original_text": "PY1\n(SU)"
            },
            issues=[]
        )

        preview.unresolved_blocks.append(unresolved)

        # Validate completeness
        issues = validate_resolution_completeness(preview)

        assert len(issues) == 1
        assert issues[0].severity == ValidationSeverity.ERROR
        assert issues[0].code == "UNRESOLVED_BLOCK_REMAINS"
        assert "tuesday" in issues[0].message.lower()
        assert "I-A" in issues[0].message
