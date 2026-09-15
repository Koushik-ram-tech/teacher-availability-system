"""Main DOCX parser implementation.

Deterministic, non-guessing parser for DOCX timetable documents.
"""

from io import BytesIO
from typing import Optional
import uuid
import re

from docx import Document
from docx.table import Table, _Cell
from docx.oxml.table import CT_Tc

from app.domain.timetable import SourceLocation, ValidationIssue, ValidationSeverity
from .staging import (
    ActivityCandidate,
    TeacherCandidate,
    ResourceCandidate,
    TimetableBlock,
    UnresolvedTimetableBlock,
    ResolvedActivity,
    DOCXImportPreview,
)
from .resolution import ResolutionRule
from .occupancy import OccupancyExtractor


def parse_docx_timetable(docx_bytes: bytes, department: str, academic_year: str, source_file: str) -> DOCXImportPreview:
    """Parse DOCX timetable document into ImportPreview.

    Args:
        docx_bytes: Raw DOCX file bytes
        department: Department name (e.g., "Computer Applications")
        academic_year: Academic year (e.g., "2026-Odd")
        source_file: Source filename

    Returns:
        DOCXImportPreview containing resolved and unresolved blocks
    """
    # Load document
    doc = Document(BytesIO(docx_bytes))

    # Create preview
    preview = DOCXImportPreview(
        import_session_id=str(uuid.uuid4()),
        academic_year=academic_year,
        department=department,
        source_file=source_file
    )

    # Find main timetable table (table 0)
    if len(doc.tables) == 0:
        preview.errors.append(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            code="NO_TABLES_FOUND",
            message="No tables found in DOCX document",
            affected_entity_type="document"
        ))
        return preview

    main_table = doc.tables[0]

    # Extract legends dynamically
    faculty_legend, resource_legend = extract_legends(doc)
    preview.faculty_legend = faculty_legend
    preview.resource_legend = resource_legend

    # Parse timetable table
    blocks = parse_timetable_table(main_table, source_file, department, faculty_legend, resource_legend)

    # Classify each block as resolved or unresolved
    # ALSO extract occupancy independently
    for block in blocks:
        ResolutionRule.classify_block(block)

        # NEW: Extract occupancy independently of resolution status
        # Subject/activity ambiguity does NOT block occupancy extraction
        occupancy_result = OccupancyExtractor.extract_occupancy(block)

        # Add teacher occupancies
        preview.teacher_occupancies.extend(occupancy_result.teacher_occupancies)

        # Add resource occupancies
        preview.resource_occupancies.extend(occupancy_result.resource_occupancies)

        # Track extraction statistics
        if occupancy_result.extraction_successful:
            preview.occupancy_extraction_success_count += 1
        else:
            preview.occupancy_extraction_blocked_count += 1

        if block.is_resolved:
            # Convert to ResolvedActivity
            resolved = convert_to_resolved_activity(block)
            if resolved:
                preview.resolved_blocks.append(resolved)
                preview.resolved_count += 1
        else:
            # Keep as unresolved
            unresolved = convert_to_unresolved_block(block)
            preview.unresolved_blocks.append(unresolved)
            preview.unresolved_count += 1

        # Collect issues
        for issue in block.issues:
            if issue.severity == ValidationSeverity.ERROR:
                preview.errors.append(issue)
            elif issue.severity == ValidationSeverity.WARNING:
                preview.warnings.append(issue)

        preview.total_blocks += 1

    return preview


def extract_legends(doc: Document) -> tuple[dict[str, str], dict[str, str]]:
    """Extract faculty and resource legends dynamically from all tables except the main one.

    Args:
        doc: The DOCX document object

    Returns:
        Tuple of (faculty_legend, resource_legend) mapping acronyms/codes to their full names/locations
    """
    faculty_legend = {}
    resource_legend = {}

    # Skip main timetable table (assuming table 0)
    for i in range(1, len(doc.tables)):
        table = doc.tables[i]

        if len(table.rows) == 0:
            continue

        header_row = table.rows[0]

        # Identify columns dynamically by header text
        name_col = -1
        acronym_col = -1
        room_col = -1
        room_loc_col = -1

        for col_idx, cell in enumerate(header_row.cells):
            text = cell.text.strip().upper()
            if "NAME" in text or "FACULTY" in text:
                name_col = col_idx
            elif "INITIAL" in text or "ACRONYM" in text:
                acronym_col = col_idx
            elif "CLASSROOM" in text or "LABORATORY" in text or "ROOM" in text or "RESOURCE" in text:
                room_col = col_idx
            elif "LOCATION" in text:
                room_loc_col = col_idx

        # Parse data rows
        for row_idx in range(1, len(table.rows)):
            try:
                row = table.rows[row_idx]

                # Extract Faculty
                if name_col != -1 and acronym_col != -1 and len(row.cells) > max(name_col, acronym_col):
                    name = row.cells[name_col].text.strip()
                    acronym = row.cells[acronym_col].text.strip().upper()
                    if acronym and name:
                        faculty_legend[acronym] = name

                # Extract Resource
                if room_col != -1 and len(row.cells) > room_col:
                    room = row.cells[room_col].text.strip().upper()
                    location = ""
                    if room_loc_col != -1 and len(row.cells) > room_loc_col:
                        location = row.cells[room_loc_col].text.strip()

                    if room:
                        resource_legend[room] = location or room
            except Exception:
                continue

    return faculty_legend, resource_legend


def parse_timetable_table(table: Table, source_file: str, department: str, faculty_legend: dict[str, str], resource_legend: dict[str, str]) -> list[TimetableBlock]:
    """Parse main timetable table into TimetableBlock objects.

    IMPORTANT: Now handles actual DOCX structure dynamically rather than
    assuming fixed 25×13 dimensions.

    Args:
        table: Main timetable table
        source_file: Source filename
        department: Department name
        faculty_legend: Faculty legend mapping
        resource_legend: Resource legend mapping

    Returns:
        List of TimetableBlock objects
    """
    blocks = []

    # Dynamically detect slot mapping from headers
    slot_map = parse_header_row(table)

    # Detect where data rows start (skip header rows)
    # Header rows typically have time ranges or "DAY"/"SECTION" labels
    data_start_row = 2  # Default: assume 2 header rows

    for row_idx in range(min(4, len(table.rows))):
        row = table.rows[row_idx]
        if len(row.cells) > 0:
            first_cell = row.cells[0].text.strip().upper()
            # Data rows start when we see actual day names
            if first_cell in ["MON", "TUE", "WED", "THU", "FRI", "SAT"]:
                data_start_row = row_idx
                break

    # Parse data rows
    current_day = None
    for row_idx in range(data_start_row, len(table.rows)):
        row = table.rows[row_idx]

        # Extract day (column 0, may be vertically merged)
        day_cell_idx = None
        for col_idx, slot_code in slot_map.items():
            if slot_code == "DAY":
                day_cell_idx = col_idx
                break

        if day_cell_idx is None:
            day_cell_idx = 0  # Default

        if day_cell_idx < len(row.cells):
            day_text = row.cells[day_cell_idx].text.strip().upper()
            if day_text:
                current_day = normalize_day(day_text)

        if not current_day:
            continue

        # Extract section (column 1 or first non-DAY column)
        section_cell_idx = None
        for col_idx, slot_code in slot_map.items():
            if slot_code == "SECTION":
                section_cell_idx = col_idx
                break

        if section_cell_idx is None:
            section_cell_idx = 1  # Default

        if section_cell_idx >= len(row.cells):
            continue

        section = row.cells[section_cell_idx].text.strip()
        if not section:
            continue

        # Parse activity cells (columns with slot mappings)
        # CRITICAL FIX: Track which physical column positions we've already processed
        # python-docx returns the SAME merged cell content for multiple column indices
        # We must skip continuation columns to avoid duplicate logical blocks
        processed_columns = set()

        for col_idx, slot_code in sorted(slot_map.items()):  # Sort to process in column order
            if slot_code in ["BREAK", "DAY", "SECTION"]:
                continue  # Skip break and metadata columns

            if col_idx >= len(row.cells):
                continue

            # CRITICAL: Skip if we've already processed this column
            # This handles the case where merged cells appear at multiple indices
            if col_idx in processed_columns:
                continue

            try:
                cell = row.cells[col_idx]
                cell_text = cell.text.strip()

                if not cell_text or cell_text in ["", "COFFEE BREAK", "LUNCH BREAK"]:
                    # Mark column as processed even if empty/break
                    processed_columns.add(col_idx)
                    continue

                # Check for gridSpan (multi-slot activity)
                slots = [slot_code]
                grid_span = get_grid_span(cell)

                # Mark this column as processed
                processed_columns.add(col_idx)

                if grid_span and grid_span > 1:
                    # CRITICAL: Mark ALL consecutive columns covered by gridSpan as processed
                    # This prevents duplicate blocks when python-docx returns the same cell
                    # at multiple indices
                    for span_offset in range(1, grid_span):
                        spanned_col_idx = col_idx + span_offset
                        processed_columns.add(spanned_col_idx)

                    # Add additional consecutive slots based on grid_span
                    # Find next slot codes in sequence
                    base_slot_num = int(slot_code[1:]) if slot_code.startswith('S') else None
                    if base_slot_num:
                        for i in range(1, grid_span):
                            next_slot = f"S{base_slot_num + i}"
                            # Verify this slot exists in our mapping
                            if next_slot in slot_map.values():
                                slots.append(next_slot)

                # Parse cell into TimetableBlock
                block = parse_activity_cell(
                    cell_text=cell_text,
                    day=current_day,
                    section=section,
                    slots=slots,
                    source_location=SourceLocation(
                        source_type="DOCX",
                        table_index=0,
                        table_row=row_idx,
                        table_col=col_idx,
                        display_context=f"{source_file} row={row_idx} col={col_idx}"
                    ),
                    department=department,
                    faculty_legend=faculty_legend,
                    resource_legend=resource_legend
                )

                if block:
                    blocks.append(block)

            except Exception as e:
                # Log parsing error but continue
                continue

    return blocks


def parse_header_row(table: Table) -> dict[int, str]:
    """Parse header row to map column indices to slot codes.

    IMPORTANT: This now dynamically detects slot columns from the actual
    DOCX structure rather than using hardcoded positions.

    Args:
        table: Timetable table

    Returns:
        Dictionary mapping column index to slot code (or "BREAK")
    """
    slot_map = {}

    # Time range to slot code mapping (institutional slots)
    # Accept both 12-hour and 24-hour formats
    time_to_slot = {
        ("08:00", "08:55"): "S1",
        ("08:55", "09:50"): "S2",
        ("09:50", "10:45"): "S3",
        ("10:45", "11:15"): "BREAK",  # Coffee break
        ("11:15", "12:10"): "S4",
        ("12:10", "13:05"): "S5",
        ("13:05", "14:00"): "BREAK",  # Lunch break
        ("14:00", "14:55"): "S6",
        ("14:55", "15:50"): "S7",
        ("15:50", "16:45"): "S8",
        ("16:45", "17:40"): "S9",
    }

    # Detect header row with time ranges (usually row 1)
    header_row_idx = 1  # Start with row 1

    header_row = table.rows[header_row_idx]

    # Parse each column header
    for col_idx in range(len(header_row.cells)):
        cell = header_row.cells[col_idx]
        text = cell.text.strip().replace('\n', ' ').replace('\r', ' ')

        # Check for break keywords
        if "COFFEE" in text.upper() or ("BREAK" in text.upper() and "LUNCH" not in text.upper()):
            slot_map[col_idx] = "BREAK"
            continue

        if "LUNCH" in text.upper():
            slot_map[col_idx] = "BREAK"
            continue

        # Extract time range - handle formats like "08.00 - 08.55" or "08:00-08:55"
        # Pattern: HH.MM or HH:MM, dash, HH.MM or HH:MM
        time_pattern = r'(\d{1,2})[\.:] ?(\d{2})\s*-\s*(\d{1,2})[\.:] ?(\d{2})'
        match = re.search(time_pattern, text)

        if match:
            start_h = match.group(1).zfill(2)
            start_m = match.group(2)
            end_h = match.group(3).zfill(2)
            end_m = match.group(4)

            # Convert to 24-hour format if needed
            start_h_int = int(start_h)
            end_h_int = int(end_h)

            # Afternoon hours (1:05 -> 13:05)
            if start_h_int < 8 and start_h_int >= 1:
                start_h_int += 12
                start_h = str(start_h_int).zfill(2)
            if end_h_int < 8 and end_h_int >= 1:
                end_h_int += 12
                end_h = str(end_h_int).zfill(2)

            start_time = f"{start_h}:{start_m}"
            end_time = f"{end_h}:{end_m}"

            # Match against slot definitions
            for (slot_start, slot_end), slot_code in time_to_slot.items():
                if start_time == slot_start and end_time == slot_end:
                    slot_map[col_idx] = slot_code
                    break
                # Also check with :05 vs :00 tolerance
                if (start_time == slot_start or start_time.replace(":00", ":05") == slot_start) and \
                   (end_time == slot_end or end_time.replace(":00", ":05") == slot_end):
                    slot_map[col_idx] = slot_code
                    break

    # Detect Day and Section columns (usually columns 0-1)
    if 0 not in slot_map:
        slot_map[0] = "DAY"
    if 1 not in slot_map:
        slot_map[1] = "SECTION"

    return slot_map


def parse_activity_cell(
    cell_text: str,
    day: str,
    section: str,
    slots: list[str],
    source_location: SourceLocation,
    department: str,
    faculty_legend: dict[str, str],
    resource_legend: dict[str, str]
) -> Optional[TimetableBlock]:
    """Parse single activity cell into TimetableBlock.

    Args:
        cell_text: Cell content
        day: Day name
        section: Section name
        slots: Slot codes
        source_location: Source location
        department: Department name
        faculty_legend: Faculty legend
        resource_legend: Resource legend

    Returns:
        TimetableBlock or None if empty
    """
    if not cell_text:
        return None

    # Split cell into lines
    lines = [line.strip() for line in cell_text.split('\n') if line.strip()]
    if not lines:
        return None

    # Extract candidates
    activity_candidates = extract_activity_candidates(lines)
    teacher_candidates = extract_teacher_candidates(lines, faculty_legend)
    resource_candidates = extract_resource_candidates(lines, resource_legend)

    # Create block
    block = TimetableBlock(
        day=day,
        section=section,
        slots=slots,
        activity_candidates=activity_candidates,
        teacher_candidates=teacher_candidates,
        resource_candidates=resource_candidates,
        is_resolved=False,  # Will be classified later
        source_location=source_location,
        original_text=cell_text
    )

    return block


def extract_activity_candidates(lines: list[str]) -> list[ActivityCandidate]:
    """Extract activity candidates from cell lines.

    CONSERVATIVE APPROACH: Preserve raw phrases when boundaries are unclear.
    Punctuation alone does NOT define activity boundaries.

    Args:
        lines: Cell lines

    Returns:
        List of ActivityCandidate objects
    """
    candidates = []

    for line in lines:
        # Skip lines with parentheses (teachers/resources)
        if '(' in line or ')' in line:
            continue

        # Skip empty lines
        if not line:
            continue

        # Check if this looks like an activity (not a room code alone)
        # Activity patterns: "DBMS", "PY1", "PE 1,2,3,4", "DS 3,4", "Library/Research Activity"
        # Room patterns: "CA1", "LAB1A", "FDC"

        # Heuristic: If line is just letters+numbers (like "CA1", "LAB1A"), might be room
        # If line has spaces, commas with text, likely activity

        # For now, take first non-parenthesized line as activity
        if not candidates:  # Only take first line as activity
            # Check for tokenization ambiguity
            is_ambiguous = has_tokenization_ambiguity(line)

            # Infer type
            entry_type = infer_entry_type(line)

            candidates.append(ActivityCandidate(
                code=line,
                inferred_type=entry_type,
                is_tokenization_ambiguous=is_ambiguous
            ))
            break

    return candidates


def has_tokenization_ambiguity(activity_phrase: str) -> bool:
    """Check if activity phrase has ambiguous boundaries.

    Examples:
        "DS 3,4" -> True (could be "DS 3" and "DS 4" or one activity)
        "PE 1,2,3,4" -> True (could be 1 or 4 activities)
        "DBMS" -> False (clear single activity)
        "Library/Research Activity" -> False (clear single activity)

    Args:
        activity_phrase: Activity text

    Returns:
        True if boundaries are ambiguous
    """
    # Pattern: Activity code followed by comma-separated numbers
    # e.g., "PE 1,2,3,4", "DS 3,4", "PY1, PE2"

    # Check for comma-separated patterns that might indicate multiple activities
    if ',' in activity_phrase:
        # If contains commas with numbers/alphanumerics, likely ambiguous
        # Pattern: "XX 1,2,3" or "PY1, PE2"
        parts = [p.strip() for p in activity_phrase.split(',')]
        if len(parts) > 1:
            # Check if parts look like separate activity codes
            # Simple heuristic: if multiple comma-separated parts with alphanumerics
            return True

    return False


def infer_entry_type(activity_text: str) -> str:
    """Infer entry type from activity text.

    Args:
        activity_text: Activity text

    Returns:
        "CLASS", "LAB", or "OTHER"
    """
    lower_text = activity_text.lower()

    if any(keyword in lower_text for keyword in ["lab", "practical"]):
        return "LAB"
    elif any(keyword in lower_text for keyword in ["tutorial", "library", "research", "cultural", "physical", "placement", "club"]):
        return "OTHER"
    else:
        return "CLASS"


def extract_teacher_candidates(lines: list[str], faculty_legend: dict[str, str]) -> list[TeacherCandidate]:
    """Extract teacher candidates from cell lines.

    Teachers are identified by parentheses. To avoid guessing, a parenthesized
    token is ONLY classified as a teacher if it appears in the faculty_legend.

    Args:
        lines: Cell lines
        faculty_legend: Faculty legend mapping

    Returns:
        List of TeacherCandidate objects
    """
    candidates = []

    # Pattern: (ACRONYM) or (ACRONYM, ACRONYM)
    teacher_pattern = re.compile(r'\(([A-Za-z0-9, ]+)\)')

    # Create a mapping of normalized legend keys to actual acronyms
    normalized_legend_keys = {k.upper().replace(' ', ''): k for k in faculty_legend.keys()}

    for line in lines:
        matches = teacher_pattern.findall(line)
        for match in matches:
            # Split by comma
            acronyms = [a.strip().upper() for a in match.split(',') if a.strip()]
            for acronym in acronyms:
                if not acronym:
                    continue

                # Normalize token (remove spaces)
                normalized_token = acronym.replace(' ', '')

                # CRITICAL RULE: A token is a teacher ONLY if the document's
                # faculty legend establishes it as one. Do NOT guess.
                if normalized_token in normalized_legend_keys:
                    actual_acronym = normalized_legend_keys[normalized_token]
                    candidates.append(TeacherCandidate(
                        acronym=actual_acronym,
                        normalized_acronym=actual_acronym,
                        is_identity_resolvable=True
                    ))

    return candidates


def extract_resource_candidates(lines: list[str], resource_legend: dict[str, str]) -> list[ResourceCandidate]:
    """Extract resource candidates from cell lines.

    Resources are typically in parentheses or on a separate line.
    To avoid guessing, a token is ONLY classified as a resource if it
    appears in the resource_legend.

    Args:
        lines: Cell lines
        resource_legend: Resource legend mapping

    Returns:
        List of ResourceCandidate objects
    """
    candidates = []

    # Pattern for parenthesized tokens: (LAB1A), (CA1)
    paren_pattern = re.compile(r'\(([A-Za-z0-9 ]+)\)')

    # Create a mapping of normalized legend keys
    normalized_legend = {normalize_resource_code(k): k for k in resource_legend.keys()}

    for line in lines:
        # Check for resources in parentheses
        matches = paren_pattern.findall(line)
        for match in matches:
            original_match = match.strip()
            normalized = normalize_resource_code(original_match)
            if normalized in normalized_legend:
                candidates.append(ResourceCandidate(
                    code=original_match,
                    normalized_code=normalized,
                    is_identity_resolvable=True
                ))

        # Also check if the entire line (without parens) is a resource in the legend
        if '(' not in line and ')' not in line:
            original_line = line.strip()
            normalized = normalize_resource_code(original_line)
            if normalized in normalized_legend:
                candidates.append(ResourceCandidate(
                    code=original_line,
                    normalized_code=normalized,
                    is_identity_resolvable=True
                ))

    return candidates


def normalize_resource_code(code: str) -> str:
    """Normalize resource code for comparison.

    Args:
        code: Raw resource code

    Returns:
        Normalized code (uppercase, no spaces)
    """
    # Remove spaces, uppercase
    return code.strip().upper().replace(' ', '')


def normalize_day(day_text: str) -> str:
    """Normalize day name to ISO format.

    Args:
        day_text: Day name (MON, TUE, etc.)

    Returns:
        ISO day name (monday, tuesday, etc.)
    """
    day_map = {
        "MON": "monday",
        "TUE": "tuesday",
        "WED": "wednesday",
        "THU": "thursday",
        "FRI": "friday",
        "SAT": "saturday",
        "SUN": "sunday"
    }
    return day_map.get(day_text.upper(), day_text.lower())


def get_grid_span(cell: _Cell) -> Optional[int]:
    """Get gridSpan attribute from cell (for multi-slot activities).

    Args:
        cell: Table cell

    Returns:
        gridSpan value or None
    """
    try:
        tc: CT_Tc = cell._element
        tcPr = tc.tcPr
        if tcPr is not None:
            gridSpan = tcPr.gridSpan
            if gridSpan is not None:
                return gridSpan.val
    except Exception:
        pass
    return None


def convert_to_resolved_activity(block: TimetableBlock) -> Optional[ResolvedActivity]:
    """Convert resolved TimetableBlock to ResolvedActivity.

    Args:
        block: Resolved TimetableBlock

    Returns:
        ResolvedActivity or None if not resolved
    """
    if not block.is_resolved:
        return None

    if not block.activity_candidates or not block.teacher_candidates:
        return None

    activity = block.activity_candidates[0]
    teacher = block.teacher_candidates[0]
    resource = block.resource_candidates[0] if block.resource_candidates else None

    return ResolvedActivity(
        day=block.day,
        section=block.section,
        slots=block.slots,
        entry_type=activity.inferred_type,
        subject_or_activity=activity.code,
        teacher_acronym=teacher.normalized_acronym,
        resource_code=resource.normalized_code if resource else None,
        source_location=block.source_location,
        original_text=block.original_text,
        manually_resolved=False
    )


def convert_to_unresolved_block(block: TimetableBlock) -> UnresolvedTimetableBlock:
    """Convert TimetableBlock to UnresolvedTimetableBlock (staging).

    Args:
        block: TimetableBlock

    Returns:
        UnresolvedTimetableBlock
    """
    return UnresolvedTimetableBlock(
        temp_id=str(uuid.uuid4()),
        day=block.day,
        section=block.section,
        slots=block.slots,
        activity_candidates=[
            {
                "code": ac.code,
                "inferred_type": ac.inferred_type,
                "is_tokenization_ambiguous": ac.is_tokenization_ambiguous
            }
            for ac in block.activity_candidates
        ],
        teacher_candidates=[
            {
                "acronym": tc.acronym,
                "normalized_acronym": tc.normalized_acronym,
                "is_identity_resolvable": tc.is_identity_resolvable
            }
            for tc in block.teacher_candidates
        ],
        resource_candidates=[
            {
                "code": rc.code,
                "normalized_code": rc.normalized_code,
                "is_identity_resolvable": rc.is_identity_resolvable
            }
            for rc in block.resource_candidates
        ],
        ambiguity_reason=block.ambiguity_reason or "Unknown",
        original_text=block.original_text,
        source_location={
            "source_type": block.source_location.source_type,
            "table_index": block.source_location.table_index,
            "table_row": block.source_location.table_row,
            "table_col": block.source_location.table_col,
            "display_context": block.source_location.display_context
        },
        issues=[
            {
                "severity": issue.severity.value if hasattr(issue.severity, 'value') else str(issue.severity),
                "code": issue.code,
                "message": issue.message,
                "affected_entity_type": issue.affected_entities.get("type", "block")
            }
            for issue in block.issues
        ]
    )
