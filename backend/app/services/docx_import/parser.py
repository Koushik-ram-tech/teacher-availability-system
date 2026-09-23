"""Structural DOCX timetable parser.

Layered parsing approach:
  DOCX table
    -> physical cell / merge structure
    -> physical-column -> logical-slot mapping (from header gridSpan)
    -> paragraph/activity groups (split on blank lines)
    -> legend-driven token classification
    -> activity | teacher/person | resource candidates
    -> occupancy extraction

Key design decisions:
  - Slot coverage derived from header structure, NOT arithmetic S{n+i}
  - Multi-activity cells preserved as ActivityGroups
  - Activity lines MAY contain inline parenthesized metadata (e.g. "CC (RR)")
  - Token regex supports punctuation: * - . (for Ind*, etc.)
  - Meghana (name-only legend entry) and Ind* (EXTERNAL) preserved
  - Comma-separated resources -> all OCCUPIED; slash-separated -> AMBIGUOUS
  - Resource-only blocks (no teacher) preserved with teacher_occupancy_status=UNSPECIFIED
  - No MCA-specific hardcoded values anywhere
"""

from io import BytesIO
from typing import Optional
import uuid
import re

from docx import Document
from docx.table import Table, _Cell

from app.domain.timetable import SourceLocation, ValidationIssue, ValidationSeverity
from .staging import (
    ActivityCandidate,
    ActivityGroup,
    OccupantRole,
    ResourceCandidate,
    TeacherCandidate,
    TimetableBlock,
    DOCXImportPreview,
)
from .resolution import ResolutionRule
from .occupancy import OccupancyExtractor
from .participation_policy import classify_activity, ActivityParticipationPolicy


# ---------------------------------------------------------------------------
# Section-code patterns to skip (e.g. SC1, SC2 are section labels, not teachers)
# ---------------------------------------------------------------------------
_SECTION_CODE_RE = re.compile(r'^SC\d+$', re.IGNORECASE)

# Token pattern: letters, digits, spaces, comma, period, asterisk, hyphen, underscore
_TOKEN_RE = re.compile(r'[A-Za-z][A-Za-z0-9 .*\-_]*')

# Pattern for a parenthesised group
_PAREN_GROUP_RE = re.compile(r'\(([^)]+)\)')


def _repair_brackets(line: str) -> str:
    """Repair mismatched parentheses caused by DOCX formatting artifacts.

    Examples:
      '(Lab1B'  → '(Lab1B)'   (unclosed open)
      'Lab1B)'  → '(Lab1B)'   (unopened close)

    Only fires when counts are unbalanced.  Does not alter well-formed lines.
    """
    opens = line.count('(')
    closes = line.count(')')
    if opens > closes:
        line = line + ')' * (opens - closes)
    elif closes > opens:
        line = '(' * (closes - opens) + line
    return line


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_docx_timetable(
    docx_bytes: bytes,
    department: str,
    academic_year: str,
    source_file: str,
) -> DOCXImportPreview:
    """Parse DOCX timetable document into DOCXImportPreview."""
    doc = Document(BytesIO(docx_bytes))

    preview = DOCXImportPreview(
        import_session_id=str(uuid.uuid4()),
        academic_year=academic_year,
        department=department,
        source_file=source_file,
    )

    if not doc.tables:
        preview.errors.append(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            code="NO_TABLES_FOUND",
            message="No tables found in DOCX document",
            affected_entity_type="document",
        ))
        return preview

    main_table = doc.tables[0]
    faculty_legend, faculty_name_only, faculty_external, resource_legend = extract_legends(doc)
    preview.faculty_legend = faculty_legend
    preview.faculty_name_only = faculty_name_only
    preview.faculty_external = faculty_external
    preview.resource_legend = resource_legend

    blocks = parse_timetable_table(
        main_table, source_file, department,
        faculty_legend, faculty_name_only, faculty_external, resource_legend,
    )

    for block in blocks:
        ResolutionRule.classify_block(block)

        # --- Activity participation policy classification ---
        # Classify BEFORE occupancy routing so STUDENT_MANAGED blocks
        # are not flagged as needing review solely due to absent teacher.
        primary_activity = block.activity_candidates[0].code if block.activity_candidates else ""
        policy = classify_activity(primary_activity)
        block.participation_policy = policy.value

        occupancy_result = OccupancyExtractor.extract_occupancy(block)

        block.teacher_allocations = occupancy_result.teacher_occupancies
        preview.teacher_occupancies.extend(occupancy_result.teacher_occupancies)

        block.resource_allocations = occupancy_result.resource_occupancies
        preview.resource_occupancies.extend(occupancy_result.resource_occupancies)

        if occupancy_result.extraction_successful:
            preview.occupancy_extraction_success_count += 1
        else:
            preview.occupancy_extraction_blocked_count += 1

        # Occupancy-first routing: only genuine teacher/resource ambiguity -> review.
        #
        # STUDENT_MANAGED special rule:
        #   Teacher absence is INTENTIONAL for student-managed activities.
        #   UNSPECIFIED teacher status is NOT a reason to send to review.
        #   Resource ambiguity is still a real issue -> still goes to review.
        teacher_needs_review = block.teacher_occupancy_status == "AMBIGUOUS"

        # For STUDENT_MANAGED: teacher UNSPECIFIED is expected, skip teacher review gate.
        # For FACULTY_MANAGED / UNKNOWN: teacher UNSPECIFIED with no candidates may still
        # be OK (resource-only blocks), so we don't add extra strictness.
        resource_needs_review = block.resource_occupancy_status == "AMBIGUOUS"
        extraction_blocked = not occupancy_result.extraction_successful

        needs_review = teacher_needs_review or resource_needs_review or extraction_blocked

        if needs_review:
            preview.occupancy_review_blocks.append(block)
            preview.occupancy_review_count += 1
        else:
            preview.occupancy_ready_blocks.append(block)
            preview.occupancy_ready_count += 1

        for issue in block.issues:
            if issue.severity == ValidationSeverity.ERROR:
                preview.errors.append(issue)
            elif issue.severity == ValidationSeverity.WARNING:
                preview.warnings.append(issue)

        preview.total_blocks += 1

    return preview


# ---------------------------------------------------------------------------
# Legend extraction
# ---------------------------------------------------------------------------

def extract_legends(
    doc: Document,
) -> tuple[dict, dict, dict, dict]:
    """Extract faculty and resource legends from all non-main tables.

    Returns:
        (faculty_legend, faculty_name_only, faculty_external, resource_legend)

        faculty_legend   : acronym -> full_name  (FACULTY role)
        faculty_name_only: normalized_name -> full_name  (no acronym, e.g. Meghana)
        faculty_external : token -> description  (EXTERNAL role, e.g. Ind*)
        resource_legend  : normalized_code -> location/description
    """
    faculty_legend: dict[str, str] = {}
    faculty_name_only: dict[str, str] = {}
    faculty_external: dict[str, str] = {}
    resource_legend: dict[str, str] = {}

    for i in range(1, len(doc.tables)):
        table = doc.tables[i]
        if not table.rows:
            continue

        header_row = table.rows[0]
        name_col = acronym_col = room_col = room_loc_col = -1

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

        for row_idx in range(1, len(table.rows)):
            try:
                row = table.rows[row_idx]

                # Faculty entry
                if name_col != -1 and len(row.cells) > name_col:
                    name = row.cells[name_col].text.strip()
                    acronym = ""
                    if acronym_col != -1 and len(row.cells) > acronym_col:
                        acronym = row.cells[acronym_col].text.strip()

                    if name:
                        if acronym:
                            acronym_upper = acronym.upper().replace(" ", "")
                            # Detect EXTERNAL role (e.g. "Ind*", "Industry Person")
                            if "*" in acronym or "IND" in acronym_upper:
                                faculty_external[acronym] = name
                            else:
                                faculty_legend[acronym] = name
                        else:
                            # NAME_ONLY entry (no acronym in legend, e.g. Meghana)
                            normalized = name.strip().upper().replace(" ", "")
                            faculty_name_only[normalized] = name

                # Resource entry
                if room_col != -1 and len(row.cells) > room_col:
                    room = row.cells[room_col].text.strip()
                    location = ""
                    if room_loc_col != -1 and len(row.cells) > room_loc_col:
                        location = row.cells[room_loc_col].text.strip()
                    if room:
                        norm = normalize_resource_code(room)
                        resource_legend[norm] = location or room
            except Exception:
                continue

    return faculty_legend, faculty_name_only, faculty_external, resource_legend


# ---------------------------------------------------------------------------
# Physical column -> slot mapping
# ---------------------------------------------------------------------------

def build_physical_col_slot_map(table: Table) -> list[Optional[str]]:
    """Build a flat list mapping each physical column index to its slot label.

    This accounts for header cells that span multiple physical columns (gridSpan).
    The resulting list has one entry per physical column.

    Example:
      header col 0: span=1 -> DAY
      header col 1: span=1 -> SECTION
      header col 2: span=1 -> S1
      ...
      header col 9: span=1 -> S7
      header col 10: span=2 -> S8   (physical cols 10,11 both map to S8)
      header col 12: span=2 -> S9   (physical cols 12,13 both map to S9)

    Returns:
        List where index = physical column, value = slot label or None.
    """
    time_to_slot = {
        ("08:00", "08:55"): "S1",
        ("08:55", "09:50"): "S2",
        ("09:50", "10:45"): "S3",
        ("10:45", "11:15"): "BREAK",
        ("11:15", "12:10"): "S4",
        ("12:10", "13:05"): "S5",
        ("13:05", "14:00"): "BREAK",
        ("14:00", "14:55"): "S6",
        ("14:55", "15:50"): "S7",
        ("15:50", "16:45"): "S8",
        ("16:45", "17:40"): "S9",
    }

    # Find the header row: look for any row in the first 4 rows that contains time ranges
    # (Supports both single-row and double-row header layouts)
    header_row = None
    time_re_detect = re.compile(r'\d{1,2}[.:]\d{2}\s*[-]\s*\d{1,2}[.:]\d{2}')
    for h_idx in range(min(4, len(table.rows))):
        row_text = " ".join(table.rows[h_idx]._tr.itertext())
        if time_re_detect.search(row_text):
            header_row = table.rows[h_idx]
            break
    if header_row is None:
        # Fallback: use row 1 if available, else row 0
        header_row = table.rows[1] if len(table.rows) >= 2 else table.rows[0]

    # Build the flat list using XML <w:tc> elements (NOT header_row.cells which repeats merged cells).
    # python-docx's row.cells virtual list repeats gridSpan cells, giving wrong spans.
    flat: list[Optional[str]] = []
    time_re = re.compile(r'(\d{1,2})[.:](\d{2})\s*-\s*(\d{1,2})[.:](\d{2})')

    for tc_elem in header_row._tr.findall(f"{_WNS}tc"):
        span = _tc_span(tc_elem)
        text = _tc_text(tc_elem).replace('\n', ' ').strip()
        label = None
        upper = text.upper()
        if "DAY" in upper:
            label = "DAY"
        elif "SECTION" in upper or upper.strip() == "SEM":
            label = "SECTION"
        elif "COFFEE" in upper or ("BREAK" in upper and "LUNCH" not in upper):
            label = "BREAK"
        elif "LUNCH" in upper:
            label = "BREAK"
        else:
            m = time_re.search(text)
            if m:
                sh, sm, eh, em = m.group(1).zfill(2), m.group(2), m.group(3).zfill(2), m.group(4)
                sh_i, eh_i = int(sh), int(eh)
                if 1 <= sh_i <= 7:
                    sh = str(sh_i + 12).zfill(2)
                if 1 <= eh_i <= 7:
                    eh = str(eh_i + 12).zfill(2)
                start_t, end_t = f"{sh}:{sm}", f"{eh}:{em}"
                label = time_to_slot.get((start_t, end_t))
        for _ in range(span):
            flat.append(label)

        # If the flat map is empty or missing DAY/SECTION, synthesize defaults at front
    if not flat:
        return flat
    if flat[0] is None:
        flat[0] = "DAY"
    if len(flat) > 1 and flat[1] is None:
        flat[1] = "SECTION"

    return flat


def get_covered_slots(col_idx: int, span: int, col_slot_map: list[Optional[str]]) -> list[str]:
    """Get unique, ordered logical slot labels for a cell at (col_idx, span).

    Args:
        col_idx: Physical start column of the cell.
        span: gridSpan of the cell.
        col_slot_map: Flat physical-col -> slot-label map.

    Returns:
        Ordered, deduplicated list of working slot labels (excludes BREAK, DAY, SECTION, None).
    """
    seen: set[str] = set()
    slots: list[str] = []
    for offset in range(span):
        phys = col_idx + offset
        if phys < len(col_slot_map):
            label = col_slot_map[phys]
            if label and label not in ("BREAK", "DAY", "SECTION") and label not in seen:
                seen.add(label)
                slots.append(label)
    return slots


# ---------------------------------------------------------------------------
# Main table parser
# ---------------------------------------------------------------------------

_WNS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _tc_span(tc_elem) -> int:
    """Return gridSpan value for a raw <w:tc> XML element."""
    tcPr = tc_elem.find(f"{_WNS}tcPr")
    if tcPr is None:
        return 1
    gs = tcPr.find(f"{_WNS}gridSpan")
    if gs is None:
        return 1
    return int(gs.get(f"{_WNS}val", 1))


def _tc_text(tc_elem) -> str:
    """Extract plain text from a raw <w:tc> element (paragraphs and line-breaks joined by newlines).

    Handles both:
    - Multiple <w:p> elements (real DOCX)
    - <w:br/> line breaks within a single run (test fixtures using cell.text = "a\nb\nc")
    """
    lines = []
    for p in tc_elem.findall(f".//{_WNS}p"):
        current = []
        for child in p.iter():
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "t" and child.text:
                current.append(child.text)
            elif tag == "br":
                lines.append("".join(current))
                current = []
        lines.append("".join(current))
    return "\n".join(lines)


def _tc_paragraphs(tc_elem) -> list:
    """Extract list of paragraph/line text strings from a raw <w:tc> element.

    Handles both:
    - Multiple <w:p> elements (real DOCX with separate paragraphs)
    - <w:br/> line breaks within a single run (test fixtures)

    Returns one entry per logical line (paragraph or br-delimited line).
    """
    result = []
    for p in tc_elem.findall(f".//{_WNS}p"):
        current = []
        for child in p.iter():
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "t" and child.text:
                current.append(child.text)
            elif tag == "br":
                result.append("".join(current))
                current = []
        result.append("".join(current))
    return result


def parse_timetable_table(
    table: Table,
    source_file: str,
    department: str,
    faculty_legend: dict,
    faculty_name_only: dict,
    faculty_external: dict,
    resource_legend: dict,
) -> list[TimetableBlock]:
    """Parse main timetable table into TimetableBlock objects.

    CRITICAL: Iterates the raw XML <w:tc> elements (not row.cells) to get
    accurate physical column positions and gridSpan values.
    python-docx row.cells returns a virtual list that repeats merged cells —
    we must use the XML to avoid duplicates.
    """
    blocks: list[TimetableBlock] = []

    # Build physical col -> slot label map from header
    col_slot_map = build_physical_col_slot_map(table)

    # Detect data start row (first row where col 0 is a day abbreviation)
    data_start_row = 1  # default: start from row 1
    for row_idx in range(min(5, len(table.rows))):
        first_tc = table.rows[row_idx]._tr.findall(f"{_WNS}tc")
        if first_tc:
            text = _tc_text(first_tc[0]).strip().upper()
            if text[:3] in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"):
                data_start_row = row_idx
                break

    # -----------------------------------------------------------------------
    # vMerge pre-processing pass
    # -----------------------------------------------------------------------
    # Build a map: (row_idx, phys_col) -> anchor tc element
    # A vMerge continuation cell has <w:vMerge/> (no w:val attribute, or val != "restart").
    # An anchor cell has <w:vMerge w:val="restart"/> or no vMerge element with content.
    # We scan column-by-column (downward) and record the most recent anchor for each
    # physical column.  When a continuation is encountered it is mapped to that anchor.
    #
    # This allows the main loop to substitute blank vMerge cells with anchor content,
    # producing the correct block for the section the continuation row belongs to.

    vmerge_anchor_map: dict[tuple[int, int], object] = {}  # (row, phys_col) -> anchor tc elem

    # Per-column tracker: phys_col -> (last_anchor_tc, last_anchor_span)
    col_anchor: dict[int, object] = {}

    for row_idx in range(data_start_row, len(table.rows)):
        tr = table.rows[row_idx]._tr
        tc_elements = tr.findall(f"{_WNS}tc")
        phys_col = 0
        for tc_elem in tc_elements:
            span = _tc_span(tc_elem)
            # Check for vMerge element
            tcPr = tc_elem.find(f"{_WNS}tcPr")
            vmerge = tcPr.find(f"{_WNS}vMerge") if tcPr is not None else None
            if vmerge is not None:
                val = vmerge.get(f"{_WNS}val", "")
                if val == "restart":
                    # Anchor cell — register for each physical column it covers
                    for offset in range(span):
                        col_anchor[phys_col + offset] = tc_elem
                else:
                    # Continuation cell — map to the current anchor for this phys_col
                    anchor = col_anchor.get(phys_col)
                    if anchor is not None:
                        vmerge_anchor_map[(row_idx, phys_col)] = anchor
            else:
                # No vMerge — treat as anchor (resets the column tracker)
                for offset in range(span):
                    col_anchor[phys_col + offset] = tc_elem
            phys_col += span

    # -----------------------------------------------------------------------
    # Main row-parsing loop
    # -----------------------------------------------------------------------

    current_day: Optional[str] = None

    for row_idx in range(data_start_row, len(table.rows)):
        tr = table.rows[row_idx]._tr
        tc_elements = tr.findall(f"{_WNS}tc")

        if not tc_elements:
            continue

        # Day (first column, may be vMerge -> blank text -> keep current_day)
        day_text = _tc_text(tc_elements[0]).strip().upper()
        if day_text:
            current_day = normalize_day(day_text)
        if not current_day:
            continue

        # Section (second column)
        if len(tc_elements) < 2:
            continue
        section = _tc_text(tc_elements[1]).strip().replace("\n", " ").strip()
        if not section:
            continue

        # Iterate actual tc elements in order, tracking absolute physical column
        phys_col = 0
        for tc_elem in tc_elements:
            span = _tc_span(tc_elem)
            cell_col = phys_col
            phys_col += span

            # Determine logical slots covered by this cell
            slots = get_covered_slots(cell_col, span, col_slot_map)
            if not slots:
                continue  # DAY / SECTION / BREAK / unmapped

            cell_text = _tc_text(tc_elem).strip()

            # vMerge continuation substitution:
            # If this cell is blank, check whether it maps to an anchor.
            # If so, substitute the anchor's content so this section-row gets a block.
            effective_tc = tc_elem
            if not cell_text:
                anchor_tc = vmerge_anchor_map.get((row_idx, cell_col))
                if anchor_tc is not None:
                    effective_tc = anchor_tc
                    cell_text = _tc_text(anchor_tc).strip()

            if not cell_text or _is_break_text(cell_text):
                continue

            # _tc_paragraphs returns one entry per <w:p> element.
            # Some test fixtures write "Line1\nLine2\nLine3" as a single paragraph text.
            # Expand any embedded newlines so parse_paragraph_group sees them separately.
            raw_paragraphs = _tc_paragraphs(effective_tc)
            cell_paragraphs = []
            for para in raw_paragraphs:
                cell_paragraphs.extend(para.split("\n"))

            block = parse_activity_cell(
                paragraphs=cell_paragraphs,
                cell_text=cell_text,
                day=current_day,
                section=section,
                slots=slots,
                source_location=SourceLocation(
                    source_type="DOCX",
                    table_index=0,
                    table_row=row_idx,
                    table_col=cell_col,
                    display_context=f"{source_file} row={row_idx} col={cell_col}",
                ),
                department=department,
                faculty_legend=faculty_legend,
                faculty_name_only=faculty_name_only,
                faculty_external=faculty_external,
                resource_legend=resource_legend,
            )

            if block:
                blocks.append(block)

    return blocks



# ---------------------------------------------------------------------------
# Cell parser
# ---------------------------------------------------------------------------

def _is_break_text(text: str) -> bool:
    """Return True if cell text represents a COFFEE/LUNCH break."""
    upper = text.upper().replace(" ", "")
    return "COFFEEBREAK" in upper or "LUNCHBREAK" in upper or upper in ("COFFEE", "LUNCH")


def parse_activity_cell(
    paragraphs: list[str],
    cell_text: str,
    day: str,
    section: str,
    slots: list[str],
    source_location: SourceLocation,
    department: str,
    faculty_legend: dict,
    faculty_name_only: dict,
    faculty_external: dict,
    resource_legend: dict,
) -> Optional[TimetableBlock]:
    """Parse a single activity cell into a TimetableBlock.

    Handles:
    - Simple cells (single activity)
    - Complex cells with multiple activity groups separated by blank paragraphs
    - Activity lines with inline parenthesised metadata (e.g. "CC (RR)")
    """
    if not cell_text:
        return None

    # Split paragraphs into activity groups on blank-paragraph boundaries
    groups_raw: list[list[str]] = []
    current_group: list[str] = []
    for para in paragraphs:
        stripped = para.strip()
        if not stripped:
            if current_group:
                groups_raw.append(current_group)
                current_group = []
        else:
            current_group.append(stripped)
    if current_group:
        groups_raw.append(current_group)

    if not groups_raw:
        return None

    activity_groups: list[ActivityGroup] = []
    all_activities: list[ActivityCandidate] = []
    all_teachers: list[TeacherCandidate] = []
    all_resources: list[ResourceCandidate] = []
    all_unknown: list[str] = []

    for group_lines in groups_raw:
        group = parse_paragraph_group(
            lines=group_lines,
            faculty_legend=faculty_legend,
            faculty_name_only=faculty_name_only,
            faculty_external=faculty_external,
            resource_legend=resource_legend,
        )
        activity_groups.append(group)
        all_activities.extend(group.activity_candidates)
        all_teachers.extend(group.teacher_candidates)
        all_resources.extend(group.resource_candidates)
        all_unknown.extend(group.unknown_tokens)

    # Deduplicate while preserving order
    all_teachers = _dedup_teachers(all_teachers)
    all_resources = _dedup_resources(all_resources)

    if not all_activities and not all_teachers and not all_resources:
        return None

    return TimetableBlock(
        day=day,
        section=section,
        slots=slots,
        activity_candidates=all_activities,
        teacher_candidates=all_teachers,
        resource_candidates=all_resources,
        activity_groups=activity_groups,
        unknown_tokens=all_unknown,
        source_location=source_location,
        original_text=cell_text,
    )


def _dedup_teachers(candidates: list[TeacherCandidate]) -> list[TeacherCandidate]:
    """Deduplicate by normalized_acronym, preserving first occurrence."""
    seen: set[str] = set()
    result = []
    for c in candidates:
        key = c.normalized_acronym
        if key not in seen:
            seen.add(key)
            result.append(c)
    return result


def _dedup_resources(candidates: list[ResourceCandidate]) -> list[ResourceCandidate]:
    """Deduplicate by normalized_code, preserving first occurrence."""
    seen: set[str] = set()
    result = []
    for c in candidates:
        key = c.normalized_code
        if key not in seen:
            seen.add(key)
            result.append(c)
    return result


# ---------------------------------------------------------------------------
# Paragraph group parser
# ---------------------------------------------------------------------------

def parse_paragraph_group(
    lines: list[str],
    faculty_legend: dict,
    faculty_name_only: dict,
    faculty_external: dict,
    resource_legend: dict,
) -> ActivityGroup:
    """Parse one paragraph group (split from a complex cell) into an ActivityGroup.

    A paragraph group looks like:
      Line 0: Activity text (may contain inline parens)
      Line 1+: Parenthesised tokens (teachers, resources, section-codes, etc.)

    All extracted teachers/resources must be legend-driven.
    Unresolved tokens are stored in unknown_tokens (never silently dropped).
    """
    raw_text = "\n".join(lines)
    group = ActivityGroup(raw_text=raw_text)

    # Build normalized lookup sets
    norm_faculty: dict[str, str] = {k.upper().replace(" ", ""): k for k in faculty_legend}
    norm_name_only: dict[str, str] = {k: v for k, v in faculty_name_only.items()}  # already normalized
    norm_external: dict[str, str] = {k.upper().replace(" ", ""): k for k in faculty_external}
    norm_resources: dict[str, str] = {k: k for k in resource_legend}  # key already normalized

    for line in lines:
        if not line:
            continue

        # Strip document annotation markers (**... ***...)
        clean_line = re.sub(r'^\*+', '', line).strip()

        # Repair mismatched parentheses before regex extraction
        clean_line = _repair_brackets(clean_line)

        # Check: does the line contain any parenthesised content?
        paren_matches = _PAREN_GROUP_RE.findall(clean_line)

        if paren_matches:
            # Line has parenthesised tokens — extract activity prefix and classify tokens
            # Activity prefix = text before the first '('
            prefix = clean_line[:clean_line.index('(')].strip() if '(' in clean_line else ""

            if prefix:
                # Prefix is an activity (e.g. "CC" in "CC (RR)" or "ELE-3 (BC)" in multi)
                ac = _make_activity_candidate(prefix)
                if ac:
                    group.activity_candidates.append(ac)

            # Classify each parenthesised group
            for paren_content in paren_matches:
                _classify_paren_group(
                    paren_content=paren_content,
                    group=group,
                    norm_faculty=norm_faculty,
                    norm_name_only=norm_name_only,
                    norm_external=norm_external,
                    norm_resources=norm_resources,
                    faculty_legend=faculty_legend,
                    faculty_name_only=faculty_name_only,
                    faculty_external=faculty_external,
                )
        else:
            # No parentheses — this is a pure activity line or bare resource line
            clean = clean_line.strip()
            if not clean:
                continue

            norm = normalize_resource_code(clean)
            if norm in norm_resources:
                # Bare resource line (no parens)
                group.resource_candidates.append(ResourceCandidate(
                    code=clean,
                    normalized_code=norm,
                    is_identity_resolvable=True,
                ))
            else:
                # Activity text
                ac = _make_activity_candidate(clean)
                if ac:
                    group.activity_candidates.append(ac)

    return group


def _make_activity_candidate(text: str) -> Optional[ActivityCandidate]:
    """Create an ActivityCandidate from activity text."""
    clean = text.strip().strip('*').strip()
    if not clean:
        return None
    is_ambiguous = has_tokenization_ambiguity(clean)
    entry_type = infer_entry_type(clean)
    return ActivityCandidate(
        code=clean,
        inferred_type=entry_type,
        is_tokenization_ambiguous=is_ambiguous,
    )


def _classify_paren_group(
    paren_content: str,
    group: ActivityGroup,
    norm_faculty: dict,
    norm_name_only: dict,
    norm_external: dict,
    norm_resources: dict,
    faculty_legend: dict,
    faculty_name_only: dict,
    faculty_external: dict,
) -> None:
    """Classify the tokens inside one parenthesised group.

    The group may be comma-separated tokens (e.g. "VK, RMR, RR") or
    slash-separated alternatives (e.g. "CA1/CA2").

    Tokens are classified as:
    - FACULTY teacher (in faculty legend by acronym)
    - EXTERNAL teacher (in faculty_external)
    - NAME_ONLY teacher (in faculty_name_only by name)
    - SECTION_CODE (e.g. SC1, SC2 — skip silently, not teachers)
    - RESOURCE (in resource_legend)
    - UNKNOWN (preserve in unknown_tokens, never drop)

    Slash-separated groups are treated as genuinely ambiguous resource alternatives.
    """
    content = paren_content.strip()
    if not content:
        return

    # Check for slash-separated resource alternatives
    is_slash_list = "/" in content and "," not in content
    if is_slash_list:
        tokens = [t.strip() for t in content.split("/") if t.strip()]
        is_ambiguous = True
    else:
        # Comma-separated list
        tokens = [t.strip() for t in content.split(",") if t.strip()]
        is_ambiguous = False

    for raw_token in tokens:
        if not raw_token:
            continue

        norm_tok = raw_token.upper().replace(" ", "")

        # 1. Section code — skip (not a person)
        if _SECTION_CODE_RE.match(norm_tok):
            continue

        # 2. EXTERNAL role (e.g. Ind*)
        if norm_tok in norm_external:
            actual = norm_external[norm_tok]
            group.teacher_candidates.append(TeacherCandidate(
                acronym=actual,
                normalized_acronym=norm_tok,
                is_identity_resolvable=True,
                role=OccupantRole.EXTERNAL,
                raw_token=raw_token,
            ))
            continue

        # 3. FACULTY by acronym
        if norm_tok in norm_faculty:
            actual = norm_faculty[norm_tok]
            group.teacher_candidates.append(TeacherCandidate(
                acronym=actual,
                normalized_acronym=norm_tok,
                is_identity_resolvable=True,
                role=OccupantRole.FACULTY,
                raw_token=raw_token,
            ))
            continue

        # 4. NAME_ONLY (e.g. Meghana)
        norm_name = norm_tok  # already uppercased, no spaces
        if norm_name in norm_name_only:
            full_name = norm_name_only[norm_name]
            group.teacher_candidates.append(TeacherCandidate(
                acronym=full_name,  # use full name as identifier
                normalized_acronym=norm_name,
                is_identity_resolvable=True,
                role=OccupantRole.FACULTY,
                is_name_only=True,
                raw_token=raw_token,
            ))
            continue

        # 5. Resource
        if norm_tok in norm_resources:
            group.resource_candidates.append(ResourceCandidate(
                code=raw_token,
                normalized_code=norm_tok,
                is_identity_resolvable=True,
                is_assignment_ambiguous=is_ambiguous,
            ))
            continue

        # 6. UNKNOWN — preserve for audit, never drop silently
        # Skip pure-number tokens (row numbers, section tags)
        if not raw_token.isdigit():
            group.unknown_tokens.append(raw_token)


# ---------------------------------------------------------------------------
# Activity helpers
# ---------------------------------------------------------------------------

def has_tokenization_ambiguity(activity_phrase: str) -> bool:
    """Return True if activity phrase has ambiguous token boundaries.

    Examples:
      "DS 3,4"      -> True  (could be "DS 3" and "DS 4" or one activity)
      "PE 1,2,3,4"  -> True
      "DBMS"        -> False
    """
    if "," in activity_phrase:
        parts = [p.strip() for p in activity_phrase.split(",")]
        if len(parts) > 1:
            return True
    return False


def infer_entry_type(activity_text: str) -> str:
    """Infer entry type from activity text."""
    lower = activity_text.lower()
    if any(k in lower for k in ["lab", "practical"]):
        return "LAB"
    if any(k in lower for k in [
        "tutorial", "library", "research", "cultural", "physical",
        "placement", "club", "vac", "extended", "mini project",
    ]):
        return "OTHER"
    return "CLASS"


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def normalize_resource_code(code: str) -> str:
    """Normalize resource code for legend comparison (uppercase, no spaces)."""
    return code.strip().upper().replace(" ", "")


def normalize_day(day_text: str) -> str:
    """Normalize day abbreviation to ISO name."""
    day_map = {
        "MON": "monday",
        "TUE": "tuesday",
        "WED": "wednesday",
        "THU": "thursday",
        "FRI": "friday",
        "SAT": "saturday",
        "SUN": "sunday",
    }
    return day_map.get(day_text.upper()[:3], day_text.lower())


def get_grid_span(cell: _Cell) -> Optional[int]:
    """Get gridSpan attribute from cell (for multi-slot activities)."""
    try:
        tc = cell._element
        tcPr = tc.tcPr
        if tcPr is not None:
            gs = tcPr.gridSpan
            if gs is not None:
                return gs.val
    except Exception:
        pass
    return None
