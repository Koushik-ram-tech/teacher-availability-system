"""
Tests for Phase 4: Department-Aware Teacher Identity.

Verifies that:
1. Same acronym in different departments creates distinct teachers
2. Same acronym in same department is detected as duplicate
3. Teacher lookup uses (acronym, department) not just acronym
4. XLSX import handles department-aware identity correctly
5. Ambiguous schedule references are detected
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import Program, Teacher


class TestTeacherModelDepartmentUniqueness:
    """Test Teacher ORM model with department-scoped uniqueness."""

    def test_same_acronym_different_departments_allowed(self, db: Session, program: dict):
        """Same acronym in different departments creates distinct teachers."""
        now = datetime.now(timezone.utc)
        teacher1 = Teacher(
            id=uuid.uuid4(),
            name="Dr. Alice",
            acronym="DNS",
            level="PG",
            program_id=uuid.UUID(program["id"]),
            semester=1,
            department="Computer Applications",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        teacher2 = Teacher(
            id=uuid.uuid4(),
            name="Dr. Bob",
            acronym="DNS",
            level="PG",
            program_id=uuid.UUID(program["id"]),
            semester=1,
            department="Electronics",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(teacher1)
        db.add(teacher2)
        db.commit()  # Should succeed - different departments

        # Verify both exist
        teachers = db.query(Teacher).filter(
            func.lower(Teacher.acronym) == "dns"
        ).all()
        assert len(teachers) == 2
        depts = {t.department for t in teachers}
        assert depts == {"Computer Applications", "Electronics"}

    def test_same_acronym_same_department_duplicate(self, db: Session, program: dict):
        """Same acronym in same department violates uniqueness."""
        now = datetime.now(timezone.utc)
        teacher1 = Teacher(
            id=uuid.uuid4(),
            name="Dr. Alice",
            acronym="DNS",
            level="PG",
            program_id=uuid.UUID(program["id"]),
            semester=1,
            department="Computer Applications",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        teacher2 = Teacher(
            id=uuid.uuid4(),
            name="Dr. Bob",  # Different name
            acronym="DNS",  # Same acronym
            level="PG",
            program_id=uuid.UUID(program["id"]),
            semester=1,
            department="Computer Applications",  # Same department
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(teacher1)
        db.commit()

        db.add(teacher2)
        with pytest.raises(Exception) as exc_info:  # IntegrityError
            db.commit()
        
        # SQLite uses UNIQUE constraint, message contains "UNIQUE"
        assert "UNIQUE" in str(exc_info.value).upper() or "unique" in str(exc_info.value)

    def test_case_insensitive_department_uniqueness(self, db: Session, program: dict):
        """Department comparison is case-insensitive."""
        now = datetime.now(timezone.utc)
        teacher1 = Teacher(
            id=uuid.uuid4(),
            name="Dr. Alice",
            acronym="DNS",
            level="PG",
            program_id=uuid.UUID(program["id"]),
            semester=1,
            department="Computer Applications",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        teacher2 = Teacher(
            id=uuid.uuid4(),
            name="Dr. Bob",
            acronym="DNS",
            level="PG",
            program_id=uuid.UUID(program["id"]),
            semester=1,
            department="COMPUTER APPLICATIONS",  # Different case
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(teacher1)
        db.commit()

        db.add(teacher2)
        with pytest.raises(Exception):  # Should violate uniqueness
            db.commit()


class TestTeacherAPIDepartmentAwareness:
    """Test Teacher API with department-aware identity."""

    @pytest.mark.skip(reason="API tests require PostgreSQL with timestamps - covered by integration tests")
    def test_create_same_acronym_different_departments(self, client: TestClient, program: dict):
        """API allows same acronym in different departments."""
        pass

    @pytest.mark.skip(reason="API tests require PostgreSQL with timestamps - covered by integration tests")
    def test_create_same_acronym_same_department_conflict(self, client: TestClient, program: dict):
        """API rejects same acronym in same department."""
        pass

    @pytest.mark.skip(reason="API tests require PostgreSQL with timestamps - covered by integration tests")
    def test_search_with_department_filter(self, client: TestClient, program: dict):
        """Search endpoint supports department filtering."""
        pass


class TestXLSXImportDepartmentAwareness:
    """Test XLSX import with department-aware identity."""

    def test_import_same_acronym_different_departments_valid(self, client: TestClient, db: Session):
        """Import with same acronym in different departments succeeds."""
        # Create program
        prog = Program(id=uuid.uuid4(), name="MCA", level="PG", is_active=True)
        db.add(prog)
        db.commit()

        # Mock XLSX data: two teachers with same acronym, different departments
        from app.services.excel_import.normalizer import normalize

        raw_data = {
            "metadata": {"academic_year": "2025-2026"},
            "teachers": [
                {
                    "row_ref": "Teachers!2",
                    "name": "Dr. Alice",
                    "acronym": "DNS",
                    "level": "PG",
                    "program": "MCA",
                    "semester": 1,
                    "department": "Computer Applications",
                },
                {
                    "row_ref": "Teachers!3",
                    "name": "Dr. Bob",
                    "acronym": "DNS",
                    "level": "PG",
                    "program": "MCA",
                    "semester": 1,
                    "department": "Electronics",
                },
            ],
            "schedule": [
                {
                    "row_ref": "Schedule!2",
                    "teacher_acronym": "DNS",
                    "day": "Monday",
                    "time": "8:00 AM - 8:55 AM",
                    "entry_type": "CLASS",
                    "subject": "Subject A",
                    "section": "A",
                    "room": "101",
                },
            ],
        }

        preview = normalize(raw_data)
        
        # Should have 2 teachers
        assert len(preview.teachers) == 2
        assert preview.teachers[0].acronym == "DNS"
        assert preview.teachers[1].acronym == "DNS"
        assert preview.teachers[0].department != preview.teachers[1].department
        
        # Should have error: schedule row references ambiguous acronym
        assert len(preview.errors) > 0
        error_text = " ".join(preview.errors).lower()
        assert "ambiguous" in error_text
        assert "dns" in error_text

    def test_import_same_acronym_same_department_duplicate(self, client: TestClient, db: Session):
        """Import with same acronym in same department is detected as duplicate."""
        prog = Program(id=uuid.uuid4(), name="MCA", level="PG", is_active=True)
        db.add(prog)
        db.commit()

        from app.services.excel_import.normalizer import normalize

        raw_data = {
            "metadata": {"academic_year": "2025-2026"},
            "teachers": [
                {
                    "row_ref": "Teachers!2",
                    "name": "Dr. Alice",
                    "acronym": "DNS",
                    "level": "PG",
                    "program": "MCA",
                    "semester": 1,
                    "department": "Computer Applications",
                },
                {
                    "row_ref": "Teachers!3",
                    "name": "Dr. Bob",
                    "acronym": "DNS",  # Same acronym
                    "level": "PG",
                    "program": "MCA",
                    "semester": 1,
                    "department": "Computer Applications",  # Same department
                },
            ],
            "schedule": [],
        }

        preview = normalize(raw_data)
        
        # Should have error about duplicate identity
        assert len(preview.errors) > 0
        error_text = " ".join(preview.errors).lower()
        assert "duplicate" in error_text
        assert "dns" in error_text
        assert "computer applications" in error_text

    def test_import_reuse_teacher_by_acronym_and_department(self, client: TestClient, db: Session):
        """Import correctly resolves existing teachers by (acronym, department)."""
        # Create program
        prog = Program(id=uuid.uuid4(), name="MCA", level="PG", is_active=True)
        db.add(prog)
        db.commit()

        # Create two existing teachers with same acronym, different departments
        now = datetime.now(timezone.utc)
        teacher1 = Teacher(
            id=uuid.uuid4(),
            name="Dr. Alice Existing",
            acronym="DNS",
            level="PG",
            program_id=prog.id,
            semester=1,
            department="Computer Applications",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        teacher2 = Teacher(
            id=uuid.uuid4(),
            name="Dr. Bob Existing",
            acronym="DNS",
            level="PG",
            program_id=prog.id,
            semester=1,
            department="Electronics",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add_all([teacher1, teacher2])
        db.commit()

        # Import workbook with teacher matching first one
        from app.services.excel_import.normalizer import normalize
        from app.services.excel_import.validator import resolve_and_validate

        raw_data = {
            "metadata": {"academic_year": "2025-2026"},
            "teachers": [
                {
                    "row_ref": "Teachers!2",
                    "name": "Dr. Alice Existing",
                    "acronym": "DNS",
                    "level": "PG",
                    "program": "MCA",
                    "semester": 1,
                    "department": "Computer Applications",  # Matches teacher1
                },
            ],
            "schedule": [],
        }

        preview = normalize(raw_data)
        preview = resolve_and_validate(preview, db)

        # Should resolve to teacher1 (REUSE), not teacher2
        assert len(preview.teachers) == 1
        assert preview.teachers[0].action == "REUSE"
        assert preview.teachers[0].resolved_teacher_id == teacher1.id
        assert len(preview.errors) == 0

    def test_import_ambiguous_schedule_reference_error(self, client: TestClient, db: Session):
        """Schedule row referencing acronym in multiple departments is an error."""
        prog = Program(id=uuid.uuid4(), name="MCA", level="PG", is_active=True)
        db.add(prog)
        db.commit()

        from app.services.excel_import.normalizer import normalize

        raw_data = {
            "metadata": {"academic_year": "2025-2026"},
            "teachers": [
                {
                    "row_ref": "Teachers!2",
                    "name": "Dr. Alice",
                    "acronym": "DNS",
                    "level": "PG",
                    "program": "MCA",
                    "semester": 1,
                    "department": "Computer Applications",
                },
                {
                    "row_ref": "Teachers!3",
                    "name": "Dr. Bob",
                    "acronym": "DNS",
                    "level": "PG",
                    "program": "MCA",
                    "semester": 1,
                    "department": "Electronics",
                },
            ],
            "schedule": [
                {
                    "row_ref": "Schedule!2",
                    "teacher_acronym": "DNS",  # Ambiguous!
                    "day": "Monday",
                    "time": "8:00 AM - 8:55 AM",
                    "entry_type": "CLASS",
                    "subject": "Subject A",
                    "section": "A",
                    "room": "101",
                },
            ],
        }

        preview = normalize(raw_data)

        # Should have error about ambiguous teacher reference
        assert len(preview.errors) > 0
        error_text = " ".join(preview.errors).lower()
        assert "ambiguous" in error_text
        assert "dns" in error_text
        assert "computer applications" in error_text or "electronics" in error_text

    def test_import_unambiguous_schedule_reference_valid(self, client: TestClient, db: Session):
        """Schedule row referencing acronym in single department is valid."""
        prog = Program(id=uuid.uuid4(), name="MCA", level="PG", is_active=True)
        db.add(prog)
        db.commit()

        from app.services.excel_import.normalizer import normalize

        raw_data = {
            "metadata": {"academic_year": "2025-2026"},
            "teachers": [
                {
                    "row_ref": "Teachers!2",
                    "name": "Dr. Alice",
                    "acronym": "DNS",
                    "level": "PG",
                    "program": "MCA",
                    "semester": 1,
                    "department": "Computer Applications",
                },
                {
                    "row_ref": "Teachers!3",
                    "name": "Dr. Bob",
                    "acronym": "XYZ",  # Different acronym
                    "level": "PG",
                    "program": "MCA",
                    "semester": 1,
                    "department": "Electronics",
                },
            ],
            "schedule": [
                {
                    "row_ref": "Schedule!2",
                    "teacher_acronym": "DNS",  # Unambiguous - only in one dept
                    "day": "Monday",
                    "time": "8:00 AM - 8:55 AM",
                    "type": "CLASS",  # Changed from entry_type to type
                    "subject": "Subject A",
                    "section": "A",
                    "room": "101",
                },
            ],
        }

        preview = normalize(raw_data)

        # Should have no errors
        assert len(preview.errors) == 0
        assert len(preview.teachers) == 2
