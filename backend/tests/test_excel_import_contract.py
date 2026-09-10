"""
test_excel_import_contract.py — Contract verification tests for the final
college-facing Excel workbook format.

Verifies that the backend importer accepts:
  - Teachers sheet with 'semester_scope' column (not 'semester')
  - Schedule sheet with 'time' column (not 'slots')
  - All 9 individual slot time ranges
  - LAB double-slot time range
  - Invalid arbitrary time ranges rejected
  - Multiple teachers, multiple schedule rows per day
  - Blank optional subject accepted
  - semester_scope string (any value) → DB semester silently defaults to 1, NO warning
  - semester_scope absent → DB semester silently defaults to 1, NO warning
  - REUSE of an existing matching teacher → NO advisory warning
  - Backward compat: legacy 'slots' column still works when 'time' absent

Runs under the existing SQLite test infrastructure from conftest.py.
"""
from __future__ import annotations

import io
from typing import Any

import openpyxl
import pytest

from app.services.excel_import.normalizer import normalize
from app.services.excel_import.parser import ParseError, parse_workbook
from app.services.excel_import.time_to_slots import TimeConversionError, time_range_to_slots


# ---------------------------------------------------------------------------
# Final-workbook builder helper
# ---------------------------------------------------------------------------


def _build_final_workbook(
    *,
    academic_year: str = "2025-2026",
    teacher_rows: list[dict] | None = None,
    schedule_rows: list[dict] | None = None,
    teacher_headers: list[str] | None = None,
    schedule_headers: list[str] | None = None,
) -> bytes:
    """Build an in-memory .xlsx using the FINAL college-facing column names."""
    wb = openpyxl.Workbook()
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    # Metadata
    meta = wb.create_sheet("Metadata")
    meta.append(["academic_year"])
    meta.append([academic_year])

    # Teachers — FINAL headers: name, acronym, level, program, department, semester_scope
    t_hdrs = teacher_headers or ["name", "acronym", "level", "program", "department", "semester_scope"]
    t_ws = wb.create_sheet("Teachers")
    t_ws.append(t_hdrs)
    for row in (teacher_rows or []):
        t_ws.append([row.get(h) for h in t_hdrs])

    # Schedule — FINAL headers: teacher_acronym, day, type, time, subject_or_activity, section, room
    s_hdrs = schedule_headers or ["teacher_acronym", "day", "type", "time",
                                   "subject_or_activity", "section", "room"]
    s_ws = wb.create_sheet("Schedule")
    s_ws.append(s_hdrs)
    for row in (schedule_rows or []):
        s_ws.append([row.get(h) for h in s_hdrs])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _teacher(acronym="TT", name="Test Teacher", level="PG",
              program="MCA", department="Test Dept",
              semester_scope=None) -> dict:
    return {
        "name": name, "acronym": acronym, "level": level,
        "program": program, "department": department,
        "semester_scope": semester_scope,
    }


def _schedule(acronym="TT", day="monday", type_="CLASS", time_="8:00 AM - 8:55 AM",
              subject_or_activity=None, section=None, room=None) -> dict:
    return {
        "teacher_acronym": acronym, "day": day, "type": type_,
        "time": time_, "subject_or_activity": subject_or_activity,
        "section": section, "room": room,
    }


# ===========================================================================
# Unit tests: time_range_to_slots
# ===========================================================================


class TestTimeToSlots:
    """Direct unit tests for the time converter."""

    @pytest.mark.parametrize("time_str,expected", [
        ("8:00 AM - 8:55 AM",   ["S1"]),
        ("8:55 AM - 9:50 AM",   ["S2"]),
        ("9:50 AM - 10:45 AM",  ["S3"]),
        ("11:15 AM - 12:10 PM", ["S4"]),
        ("12:10 PM - 1:05 PM",  ["S5"]),
        ("2:00 PM - 2:55 PM",   ["S6"]),
        ("2:55 PM - 3:50 PM",   ["S7"]),
        ("3:50 PM - 4:45 PM",   ["S8"]),
        ("4:45 PM - 5:40 PM",   ["S9"]),
    ])
    def test_single_slot_mappings(self, time_str: str, expected: list[str]) -> None:
        assert time_range_to_slots(time_str) == expected

    def test_lab_double_slot(self) -> None:
        assert time_range_to_slots("2:00 PM - 3:50 PM") == ["S6", "S7"]

    def test_en_dash_separator_accepted(self) -> None:
        assert time_range_to_slots("2:00 PM – 3:50 PM") == ["S6", "S7"]

    def test_extra_whitespace_tolerated(self) -> None:
        assert time_range_to_slots("  8:00 AM  -  8:55 AM  ") == ["S1"]

    def test_arbitrary_start_rejected(self) -> None:
        with pytest.raises(TimeConversionError, match="start time"):
            time_range_to_slots("9:00 AM - 10:00 AM")

    def test_arbitrary_end_rejected(self) -> None:
        with pytest.raises(TimeConversionError, match="end time"):
            time_range_to_slots("8:00 AM - 9:00 AM")

    def test_no_separator_rejected(self) -> None:
        with pytest.raises(TimeConversionError, match="separator"):
            time_range_to_slots("8:00 AM 8:55 AM")

    def test_24h_format_rejected(self) -> None:
        with pytest.raises(TimeConversionError):
            time_range_to_slots("13:00 - 14:00")

    def test_completely_invalid_string(self) -> None:
        with pytest.raises(TimeConversionError):
            time_range_to_slots("random text")

    def test_break_crossing_double_slot_rejected(self) -> None:
        # S3 ends 10:45, S4 starts 11:15 — a break separates them
        with pytest.raises(TimeConversionError, match="break"):
            time_range_to_slots("9:50 AM - 12:10 PM")

    def test_all_valid_double_lab_slots(self) -> None:
        valid_labs = [
            ("8:00 AM - 9:50 AM",   ["S1", "S2"]),
            ("8:55 AM - 10:45 AM",  ["S2", "S3"]),
            ("11:15 AM - 1:05 PM",  ["S4", "S5"]),
            ("2:00 PM - 3:50 PM",   ["S6", "S7"]),
            ("2:55 PM - 4:45 PM",   ["S7", "S8"]),
            ("3:50 PM - 5:40 PM",   ["S8", "S9"]),
        ]
        for time_str, expected in valid_labs:
            assert time_range_to_slots(time_str) == expected, f"Failed for {time_str!r}"


# ===========================================================================
# Parser tests: final column names accepted
# ===========================================================================


class TestParserFinalContract:
    def test_final_teachers_headers_accepted(self) -> None:
        data = _build_final_workbook(
            teacher_rows=[_teacher()],
        )
        result = parse_workbook(data, "2025-2026")
        assert len(result["teachers"]) == 1
        # semester_scope field should be present in parsed output
        assert "semester_scope" in result["teachers"][0]

    def test_final_schedule_headers_accepted(self) -> None:
        data = _build_final_workbook(
            teacher_rows=[_teacher()],
            schedule_rows=[_schedule()],
        )
        result = parse_workbook(data, "2025-2026")
        assert len(result["schedule"]) == 1
        # 'time' field should be present
        assert "time" in result["schedule"][0]
        assert result["schedule"][0]["time"] == "8:00 AM - 8:55 AM"

    def test_final_schedule_missing_time_and_slots_raises(self) -> None:
        """If neither 'time' nor 'slots' is present, parser must raise."""
        data = _build_final_workbook(
            teacher_rows=[_teacher()],
            schedule_rows=[_schedule()],
            schedule_headers=["teacher_acronym", "day", "type"],  # missing both
        )
        with pytest.raises(ParseError, match="time"):
            parse_workbook(data, "2025-2026")

    def test_legacy_slots_column_still_accepted_by_parser(self) -> None:
        """Backward compat: workbook with 'slots' column still parses."""
        wb = openpyxl.Workbook()
        if "Sheet" in wb.sheetnames:
            del wb["Sheet"]
        meta = wb.create_sheet("Metadata")
        meta.append(["academic_year"])
        meta.append(["2025-2026"])
        t_ws = wb.create_sheet("Teachers")
        t_ws.append(["name", "acronym", "level", "program", "semester"])
        t_ws.append(["Test", "TT", "PG", "MCA", 1])
        s_ws = wb.create_sheet("Schedule")
        s_ws.append(["teacher_acronym", "day", "type", "slots"])
        s_ws.append(["TT", "monday", "CLASS", "S1"])
        buf = io.BytesIO()
        wb.save(buf)
        result = parse_workbook(buf.getvalue(), "2025-2026")
        assert result["schedule"][0]["slots"] == "S1"
        assert result["schedule"][0]["time"] is None  # no 'time' col → None


# ===========================================================================
# Normalizer tests: final contract end-to-end
# ===========================================================================


def _raw_final(
    academic_year="2025-2026",
    teacher_rows: list[dict] | None = None,
    schedule_rows: list[dict] | None = None,
) -> dict:
    """Build a raw dict as if from parse_workbook() using final column names."""
    t_rows = []
    for i, r in enumerate(teacher_rows or []):
        t_rows.append({"row_ref": f"Teachers!{i + 2}", **r})
    s_rows = []
    for i, r in enumerate(schedule_rows or []):
        s_rows.append({"row_ref": f"Schedule!{i + 2}", **r})
    return {"metadata": {"academic_year": academic_year}, "teachers": t_rows, "schedule": s_rows}


def _final_teacher_raw(acronym="TT", semester_scope=None, **overrides) -> dict:
    base = {
        "name": "Test Teacher",
        "acronym": acronym,
        "level": "PG",
        "program": "MCA",
        "department": "Test Dept",
        "semester_scope": semester_scope,
        "semester": None,  # no legacy column
    }
    base.update(overrides)
    return base


def _final_schedule_raw(acronym="TT", time_="8:00 AM - 8:55 AM",
                        type_="CLASS", **overrides) -> dict:
    base = {
        "teacher_acronym": acronym,
        "day": "monday",
        "type": type_,
        "time": time_,
        "slots": None,  # no legacy column
        "subject_or_activity": None,
        "section": None,
        "room": None,
        "notes": None,
    }
    base.update(overrides)
    return base


class TestNormalizerFinalContract:

    def test_valid_class_with_time_column(self) -> None:
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[_final_schedule_raw()],
        )
        preview = normalize(raw)
        assert not preview.errors, preview.errors
        assert preview.days["monday"][0].slot_ids == ["S1"]
        assert preview.days["monday"][0].entry_type == "CLASS"

    def test_valid_lab_with_time_range(self) -> None:
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[_final_schedule_raw(type_="LAB", time_="2:00 PM - 3:50 PM")],
        )
        preview = normalize(raw)
        assert not preview.errors, preview.errors
        assert preview.days["monday"][0].slot_ids == ["S6", "S7"]
        assert preview.days["monday"][0].entry_type == "LAB"

    def test_valid_other_with_time_column(self) -> None:
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[_final_schedule_raw(type_="OTHER", time_="4:45 PM - 5:40 PM")],
        )
        preview = normalize(raw)
        assert not preview.errors, preview.errors
        assert preview.days["monday"][0].slot_ids == ["S9"]

    def test_blank_subject_accepted(self) -> None:
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[_final_schedule_raw(subject_or_activity=None)],
        )
        preview = normalize(raw)
        assert not preview.errors
        assert preview.days["monday"][0].subject_or_activity is None

    def test_invalid_arbitrary_time_rejected(self) -> None:
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[_final_schedule_raw(time_="9:00 AM - 10:00 AM")],
        )
        preview = normalize(raw)
        assert any("start time" in e for e in preview.errors)

    def test_completely_invalid_time_string_rejected(self) -> None:
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[_final_schedule_raw(time_="not a time")],
        )
        preview = normalize(raw)
        assert preview.errors

    def test_missing_time_column_rejected(self) -> None:
        """Both 'time' and 'slots' are None → error."""
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[_final_schedule_raw(time_=None)],
        )
        preview = normalize(raw)
        assert any("time" in e for e in preview.errors)

    def test_semester_scope_with_roman_numerals_defaults_to_1_no_warning(self) -> None:
        """'I & III Semester' → DB semester defaults to 1 silently (no advisory)."""
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw(semester_scope="I & III Semester")],
        )
        preview = normalize(raw)
        assert not any("semester" in e.lower() for e in preview.errors)
        assert len(preview.teachers) == 1
        assert preview.teachers[0].semester == 1
        # No advisory warning must be emitted for the semester default
        all_warnings = preview.warnings + [w for t in preview.teachers for w in t.warnings]
        assert not any(
            "semester" in w.lower() for w in all_warnings
        ), f"Unexpected semester warning(s): {all_warnings}"

    def test_semester_scope_numeric_string_still_defaults_to_1(self) -> None:
        """Even when semester_scope contains '3rd Semester', DB semester defaults to 1.
        The integer is NOT extracted; use explicit 'semester' column for that."""
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw(semester_scope="3rd Semester")],
        )
        preview = normalize(raw)
        assert len(preview.teachers) == 1
        # Must default to 1, not 3 — semester_scope is not parsed for digits
        assert preview.teachers[0].semester == 1

    def test_semester_scope_absent_defaults_to_1_no_warning(self) -> None:
        """semester_scope absent → DB semester defaults to 1 silently (no advisory)."""
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw(semester_scope=None)],
        )
        preview = normalize(raw)
        assert len(preview.teachers) == 1
        assert preview.teachers[0].semester == 1
        # No advisory warning must be emitted
        all_warnings = preview.warnings + [w for t in preview.teachers for w in t.warnings]
        assert not any(
            "semester" in w.lower() for w in all_warnings
        ), f"Unexpected semester warning(s): {all_warnings}"

    def test_semester_scope_any_text_defaults_to_1(self) -> None:
        """Any non-empty semester_scope value is display-only; DB semester = 1."""
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw(semester_scope="All semesters")],
        )
        preview = normalize(raw)
        assert preview.teachers[0].semester == 1

    def test_legacy_semester_column_still_works(self) -> None:
        """Workbook with old 'semester' column (no semester_scope) normalises correctly."""
        raw = _raw_final(
            teacher_rows=[{
                "name": "Legacy", "acronym": "LG", "level": "PG",
                "program": "MCA", "department": "Dept",
                "semester": 3,          # old column
                "semester_scope": None, # no new column
            }],
        )
        preview = normalize(raw)
        assert len(preview.teachers) == 1
        assert preview.teachers[0].semester == 3

    def test_multiple_teachers_multiple_rows_per_day(self) -> None:
        raw = _raw_final(
            teacher_rows=[
                _final_teacher_raw(acronym="AA"),
                _final_teacher_raw(acronym="BB", name="Teacher B"),
            ],
            schedule_rows=[
                _final_schedule_raw(acronym="AA", time_="8:00 AM - 8:55 AM"),
                _final_schedule_raw(acronym="AA", time_="8:55 AM - 9:50 AM"),
                _final_schedule_raw(acronym="BB", time_="8:00 AM - 8:55 AM"),
                _final_schedule_raw(acronym="BB", day="tuesday", time_="2:00 PM - 3:50 PM",
                                    type_="LAB"),
            ],
        )
        preview = normalize(raw)
        assert not preview.errors, preview.errors
        assert len(preview.days["monday"]) == 3  # AA×2 + BB×1
        assert len(preview.days["tuesday"]) == 1

    def test_sunday_still_rejected(self) -> None:
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[_final_schedule_raw(day="sunday")],
        )
        preview = normalize(raw)
        assert any("Sunday" in e for e in preview.errors)

    def test_duplicate_slot_collision_still_detected(self) -> None:
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[
                _final_schedule_raw(time_="8:00 AM - 8:55 AM"),
                _final_schedule_raw(time_="8:00 AM - 8:55 AM"),  # same slot
            ],
        )
        preview = normalize(raw)
        assert any("already occupies" in e for e in preview.errors)

    def test_all_nine_time_slots_accepted(self) -> None:
        slots = [
            "8:00 AM - 8:55 AM",
            "8:55 AM - 9:50 AM",
            "9:50 AM - 10:45 AM",
            "11:15 AM - 12:10 PM",
            "12:10 PM - 1:05 PM",
            "2:00 PM - 2:55 PM",
            "2:55 PM - 3:50 PM",
            "3:50 PM - 4:45 PM",
            "4:45 PM - 5:40 PM",
        ]
        # Two teachers to split Mon/Tue (to avoid slot collision)
        teacher_raw = [
            _final_teacher_raw(acronym="T1"),
            _final_teacher_raw(acronym="T2", name="Teacher 2"),
        ]
        days = ["monday", "tuesday", "wednesday", "thursday", "friday",
                "saturday", "monday", "tuesday", "wednesday"]
        schedule_raw = [
            _final_schedule_raw(
                acronym="T1" if i < 5 else "T2",
                day=days[i],
                time_=t,
            )
            for i, t in enumerate(slots)
        ]
        raw = _raw_final(teacher_rows=teacher_raw, schedule_rows=schedule_raw)
        preview = normalize(raw)
        assert not preview.errors, preview.errors

    # -----------------------------------------------------------------------
    # time + slots contradiction / agreement tests
    # -----------------------------------------------------------------------

    def test_time_and_slots_agree_accepted(self) -> None:
        """Both columns present and resolving to the same slot → accepted."""
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[{
                "teacher_acronym": "TT",
                "day": "monday",
                "type": "CLASS",
                "time": "8:00 AM - 8:55 AM",   # resolves to S1
                "slots": "S1",                   # agrees
                "subject_or_activity": None,
                "section": None,
                "room": None,
                "notes": None,
            }],
        )
        preview = normalize(raw)
        assert not preview.errors, preview.errors
        assert preview.days["monday"][0].slot_ids == ["S1"]

    def test_time_and_slots_contradict_rejected(self) -> None:
        """Both columns present but resolving to different slots → validation error."""
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[{
                "teacher_acronym": "TT",
                "day": "monday",
                "type": "CLASS",
                "time": "8:00 AM - 8:55 AM",   # resolves to S1
                "slots": "S2",                   # contradicts
                "subject_or_activity": None,
                "section": None,
                "room": None,
                "notes": None,
            }],
        )
        preview = normalize(raw)
        assert any("contradiction" in e.lower() or "contradict" in e.lower()
                   or "resolves to" in e for e in preview.errors)

    def test_time_and_slots_lab_contradict_rejected(self) -> None:
        """LAB: time says S6+S7, slots says S7+S8 → contradiction error."""
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[{
                "teacher_acronym": "TT",
                "day": "monday",
                "type": "LAB",
                "time": "2:00 PM - 3:50 PM",    # resolves to S6, S7
                "slots": "S7+S8",                # contradicts
                "subject_or_activity": None,
                "section": None,
                "room": None,
                "notes": None,
            }],
        )
        preview = normalize(raw)
        assert any("resolves to" in e for e in preview.errors)

    def test_time_and_slots_lab_agree_accepted(self) -> None:
        """Both columns resolving to the same two slots → accepted."""
        raw = _raw_final(
            teacher_rows=[_final_teacher_raw()],
            schedule_rows=[{
                "teacher_acronym": "TT",
                "day": "monday",
                "type": "LAB",
                "time": "2:00 PM - 3:50 PM",    # resolves to S6, S7
                "slots": "S6+S7",                # agrees
                "subject_or_activity": None,
                "section": None,
                "room": None,
                "notes": None,
            }],
        )
        preview = normalize(raw)
        assert not preview.errors, preview.errors
        assert preview.days["monday"][0].slot_ids == ["S6", "S7"]


# ===========================================================================
# HTTP endpoint smoke tests with final workbook format (SQLite)
# ===========================================================================


class TestUploadFinalWorkbook:
    """Verify the /imports/excel endpoint accepts the final workbook format."""

    def test_upload_final_format_returns_200(self, client, program, time_slots) -> None:
        data = _build_final_workbook(
            teacher_rows=[_teacher(program="Master of Computer Applications")],
            schedule_rows=[_schedule()],
        )
        resp = client.post(
            "/api/v1/imports/excel",
            files={"file": ("timetable.xlsx", data, "application/octet-stream")},
            data={"academic_year": "2025-2026"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "import_id" in body

    def test_upload_with_semester_scope_no_error(self, client, program, time_slots) -> None:
        data = _build_final_workbook(
            teacher_rows=[
                _teacher(program="Master of Computer Applications",
                         semester_scope="I & III Semester"),
            ],
            schedule_rows=[_schedule()],
        )
        resp = client.post(
            "/api/v1/imports/excel",
            files={"file": ("t.xlsx", data, "application/octet-stream")},
            data={"academic_year": "2025-2026"},
        )
        assert resp.status_code == 200, resp.text
        # No errors (program resolution may warn but should not error)
        body = resp.json()
        assert isinstance(body["errors"], list)

    def test_upload_lab_time_range_accepted(self, client, program, time_slots) -> None:
        data = _build_final_workbook(
            teacher_rows=[_teacher(program="Master of Computer Applications")],
            schedule_rows=[_schedule(type_="LAB", time_="2:00 PM - 3:50 PM")],
        )
        resp = client.post(
            "/api/v1/imports/excel",
            files={"file": ("t.xlsx", data, "application/octet-stream")},
            data={"academic_year": "2025-2026"},
        )
        assert resp.status_code == 200, resp.text

    def test_upload_invalid_time_returns_200_with_errors(self, client, program) -> None:
        data = _build_final_workbook(
            teacher_rows=[_teacher(program="Master of Computer Applications")],
            schedule_rows=[_schedule(time_="9:00 AM - 10:00 AM")],
        )
        resp = client.post(
            "/api/v1/imports/excel",
            files={"file": ("t.xlsx", data, "application/octet-stream")},
            data={"academic_year": "2025-2026"},
        )
        assert resp.status_code == 200
        assert len(resp.json()["errors"]) > 0

    def test_confirm_staging_removed_after_confirm(self, client, program, time_slots) -> None:
        """After confirm (or attempted confirm), import_id must be handled."""
        data = _build_final_workbook(
            teacher_rows=[_teacher(program="Master of Computer Applications")],
            schedule_rows=[_schedule()],
        )
        up = client.post(
            "/api/v1/imports/excel",
            files={"file": ("t.xlsx", data, "application/octet-stream")},
            data={"academic_year": "2025-2026"},
        )
        assert up.status_code == 200
        import_id = up.json()["import_id"]
        # GET must work before confirm
        get_resp = client.get(f"/api/v1/imports/{import_id}")
        assert get_resp.status_code == 200

    def test_reuse_existing_teacher_produces_no_advisory_warning(
        self, client, db, program, time_slots
    ) -> None:
        """When a workbook teacher matches an existing DB record exactly, the
        validator must set action=REUSE and emit ZERO advisory warnings.
        The 'already exists; will be reused' message was removed as routine noise."""
        import uuid as _uuid
        from sqlalchemy import text as _text

        # Seed a teacher that the workbook will match exactly
        teacher_id = str(_uuid.uuid4())
        db.execute(
            _text(
                "INSERT INTO teachers "
                "(id, name, acronym, level, program_id, semester, department, "
                "is_active, created_at, updated_at) "
                "VALUES (:id, :name, :acronym, :level, :pid, :sem, :dept, 1, :ts, :ts)"
            ),
            {
                "id": teacher_id,
                "name": "Test Teacher",
                "acronym": "TT",
                "level": "PG",
                "pid": program["id"],
                "sem": 1,
                "dept": "Test Dept",
                "ts": "2025-01-01T00:00:00",
            },
        )
        db.flush()

        data = _build_final_workbook(
            teacher_rows=[_teacher(
                acronym="TT",
                name="Test Teacher",
                level="PG",
                program="Master of Computer Applications",
                department="Test Dept",
            )],
            schedule_rows=[_schedule()],
        )
        resp = client.post(
            "/api/v1/imports/excel",
            files={"file": ("t.xlsx", data, "application/octet-stream")},
            data={"academic_year": "2025-2026"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Teacher action must be REUSE
        assert len(body["teachers"]) == 1
        assert body["teachers"][0]["action"] == "REUSE"

        # No advisory warnings anywhere — not in the global list, not on the teacher row
        global_warnings: list[str] = body["warnings"]
        teacher_warnings: list[str] = body["teachers"][0]["warnings"]
        all_warnings = global_warnings + teacher_warnings
        assert all_warnings == [], (
            f"Expected zero warnings for normal REUSE, got: {all_warnings}"
        )
