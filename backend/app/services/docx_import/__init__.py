"""DOCX timetable parser service.

This module implements deterministic parsing of DOCX timetable documents
according to DOCX_PARSER_SPECIFICATION.md.

Key principles:
- NO GUESSING: Parser never invents teacher-activity-resource associations
- NO FLATTENING: Candidates remain as individual objects
- EXPLICIT AMBIGUITY: Unresolved blocks preserved for manual resolution
- STAGING NOT DOMAIN: Structures are transport/DTO, not persistent entities
"""

from .staging import (
    ActivityCandidate,
    TeacherCandidate,
    ResourceCandidate,
    TimetableBlock,
    UnresolvedTimetableBlock,
    ResolvedActivity,
    DOCXImportPreview,
    ManualResolutionMapping,
    ExcludedCandidate,
)
from .parser import parse_docx_timetable
from .converter import convert_preview_to_canonical
from .manual_resolution import (
    apply_manual_resolution,
    apply_multiple_resolutions,
    finalize_block_resolution,
    exclude_candidate,
    validate_resolution_completeness,
)

__all__ = [
    "ActivityCandidate",
    "TeacherCandidate",
    "ResourceCandidate",
    "TimetableBlock",
    "UnresolvedTimetableBlock",
    "ResolvedActivity",
    "DOCXImportPreview",
    "ManualResolutionMapping",
    "ExcludedCandidate",
    "parse_docx_timetable",
    "convert_preview_to_canonical",
    "apply_manual_resolution",
    "apply_multiple_resolutions",
    "finalize_block_resolution",
    "exclude_candidate",
    "validate_resolution_completeness",
]
