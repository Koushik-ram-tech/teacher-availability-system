"""Tests for Availability Service.

Simple service-level tests for availability logic.
"""

import pytest

from app.services.availability import AvailabilityService, WORKING_SLOTS, WORKING_DAYS


class TestAvailabilityServiceLogic:
    """Test availability service business logic."""

    def test_working_slots_defined(self):
        """Working slots are correctly defined."""
        assert len(WORKING_SLOTS) == 9  # S1-S9
        assert "S1" in WORKING_SLOTS
        assert "S9" in WORKING_SLOTS
        assert "BREAK" not in WORKING_SLOTS

    def test_working_days_defined(self):
        """Working days exclude Sunday."""
        assert len(WORKING_DAYS) == 6  # Mon-Sat
        assert "monday" in WORKING_DAYS
        assert "saturday" in WORKING_DAYS
        assert "sunday" not in WORKING_DAYS

    def test_teacher_not_found_returns_none(self, db):
        """get_teacher_availability returns None for unknown teacher."""
        from uuid import uuid4
        result = AvailabilityService.get_teacher_availability(
            db=db,
            teacher_id=uuid4(),
            academic_year="2026-Odd"
        )
        assert result is None

    def test_resource_code_normalization(self):
        """Resource code normalization works correctly."""
        assert AvailabilityService._normalize_resource_code("LAB1A") == "lab1a"
        assert AvailabilityService._normalize_resource_code("LAB 1A") == "lab 1a"
        assert AvailabilityService._normalize_resource_code("lab 1a") == "lab 1a"
        assert AvailabilityService._normalize_resource_code("  LAB  1A  ") == "lab 1a"
