"""PostgreSQL regression tests for confirmed timetable occupancy provenance."""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.config import get_settings
from app.db import _sqlalchemy_url


@pytest.fixture
def occupancy_function_connection():
    engine = create_engine(_sqlalchemy_url(get_settings().database_url))
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.skip("PostgreSQL is required to exercise the deferred occupancy function")

    try:
        connection = engine.connect()
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"PostgreSQL is unavailable: {exc}")

    transaction = connection.begin()
    try:
        connection.execute(text("SET LOCAL search_path TO pg_temp, public"))
        connection.exec_driver_sql(
            "CREATE TEMP TABLE timetables (id UUID PRIMARY KEY, status TEXT NOT NULL) ON COMMIT DROP"
        )
        connection.exec_driver_sql(
            "CREATE TEMP TABLE schedule_entries ("
            "id UUID PRIMARY KEY, timetable_id UUID NOT NULL, day_of_week SMALLINT NOT NULL, "
            "subject_or_activity TEXT NOT NULL, section TEXT, entry_type TEXT NOT NULL, "
            "group_index SMALLINT, source_cell_text TEXT) ON COMMIT DROP"
        )
        connection.exec_driver_sql(
            "CREATE TEMP TABLE schedule_entry_slots ("
            "schedule_entry_id UUID NOT NULL, time_slot_id UUID NOT NULL) ON COMMIT DROP"
        )
        yield connection
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        engine.dispose()


def _insert_timetable_rows(connection, timetable_id, slot_id, *, second_source, second_group, second_activity):
    first_entry_id, second_entry_id = uuid4(), uuid4()
    connection.execute(
        text("INSERT INTO timetables (id, status) VALUES (:id, 'CONFIRMED')"),
        {"id": timetable_id},
    )
    connection.execute(
        text(
            "INSERT INTO schedule_entries "
            "(id, timetable_id, day_of_week, subject_or_activity, section, entry_type, group_index, source_cell_text) "
            "VALUES (:id, :timetable_id, 4, 'Database Systems', 'III-A', 'CLASS', 0, 'merged-source-cell')"
        ),
        {"id": first_entry_id, "timetable_id": timetable_id},
    )
    connection.execute(
        text(
            "INSERT INTO schedule_entries "
            "(id, timetable_id, day_of_week, subject_or_activity, section, entry_type, group_index, source_cell_text) "
            "VALUES (:id, :timetable_id, 4, :subject, 'III-B', 'CLASS', :group_index, :source_cell_text)"
        ),
        {
            "id": second_entry_id,
            "timetable_id": timetable_id,
            "subject": second_activity,
            "group_index": second_group,
            "source_cell_text": second_source,
        },
    )
    connection.execute(
        text("INSERT INTO schedule_entry_slots VALUES (:entry_id, :slot_id)"),
        [{"entry_id": first_entry_id, "slot_id": slot_id}, {"entry_id": second_entry_id, "slot_id": slot_id}],
    )


def test_postgres_allows_same_provenance_shared_occurrence(occupancy_function_connection):
    timetable_id, slot_id = uuid4(), uuid4()
    _insert_timetable_rows(
        occupancy_function_connection,
        timetable_id,
        slot_id,
        second_source="merged-source-cell",
        second_group=0,
        second_activity="Database Systems",
    )

    result = occupancy_function_connection.scalar(
        text("SELECT public.validate_timetable_slot_occupancy(:timetable_id)"),
        {"timetable_id": timetable_id},
    )

    assert result in (None, "")


def test_postgres_rejects_different_activity_or_group_same_slot(occupancy_function_connection):
    timetable_id, slot_id = uuid4(), uuid4()
    _insert_timetable_rows(
        occupancy_function_connection,
        timetable_id,
        slot_id,
        second_source="different-source-cell",
        second_group=1,
        second_activity="Different Activity",
    )

    with pytest.raises(DBAPIError):
        with occupancy_function_connection.begin_nested():
            occupancy_function_connection.execute(
                text("SELECT public.validate_timetable_slot_occupancy(:timetable_id)"),
                {"timetable_id": timetable_id},
            )