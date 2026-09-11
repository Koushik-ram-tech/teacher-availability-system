from datetime import date, datetime, time
from typing import Optional
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, SmallInteger, String, Time, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.db import Base


class Program(Base):
    __tablename__ = "programs"
    __table_args__ = (
        UniqueConstraint("id", "level", name="programs_id_level_unique"),
        CheckConstraint("btrim(name) <> ''", name="programs_name_not_blank"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    teachers: Mapped[list["Teacher"]] = relationship(back_populates="program")


class Teacher(Base):
    __tablename__ = "teachers"
    __table_args__ = (
        ForeignKeyConstraint(["program_id", "level"], ["programs.id", "programs.level"], name="teachers_program_level_fk"),
        CheckConstraint("level IN ('UG', 'PG')", name="teachers_level_check"),
        CheckConstraint("semester > 0", name="teachers_semester_check"),
        CheckConstraint("btrim(name) <> ''", name="teachers_name_not_blank"),
        CheckConstraint("btrim(acronym) <> ''", name="teachers_acronym_not_blank"),
        CheckConstraint("btrim(department) <> ''", name="teachers_department_not_blank"),
        # NOTE: Uniqueness constraint is (normalized_acronym, normalized_department)
        # enforced by database index: uq_teachers_acronym_department_normalized
        # Teachers with same acronym can exist in different departments
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    acronym: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[str] = mapped_column(String, nullable=False)
    program_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    semester: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    department: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'Prototype Department'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    program: Mapped[Program] = relationship(back_populates="teachers")
    timetables: Mapped[list["Timetable"]] = relationship(back_populates="teacher", cascade="all, delete-orphan")


class TimeSlot(Base):
    __tablename__ = "time_slots"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    entry_links: Mapped[list["ScheduleEntrySlot"]] = relationship(back_populates="time_slot")


class Timetable(Base):
    __tablename__ = "timetables"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    teacher_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False)
    academic_year: Mapped[str] = mapped_column(String, nullable=False)
    effective_from: Mapped[Optional[date]] = mapped_column(Date)
    effective_to: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    teacher: Mapped[Teacher] = relationship(back_populates="timetables")
    entries: Mapped[list["ScheduleEntry"]] = relationship(back_populates="timetable", cascade="all, delete-orphan")


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    timetable_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("timetables.id", ondelete="CASCADE"), nullable=False)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    entry_type: Mapped[str] = mapped_column(String, nullable=False)
    subject_or_activity: Mapped[str] = mapped_column(String, nullable=False)
    section: Mapped[Optional[str]] = mapped_column(String)
    room: Mapped[Optional[str]] = mapped_column(String)
    notes: Mapped[Optional[str]] = mapped_column(String)
    resource_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("resources.id"), nullable=True)

    timetable: Mapped[Timetable] = relationship(back_populates="entries")
    slot_links: Mapped[list["ScheduleEntrySlot"]] = relationship(back_populates="schedule_entry", cascade="all, delete-orphan")
    resource: Mapped[Optional["Resource"]] = relationship(back_populates="schedule_entries")


class ScheduleEntrySlot(Base):
    __tablename__ = "schedule_entry_slots"

    schedule_entry_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("schedule_entries.id", ondelete="CASCADE"), primary_key=True)
    time_slot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("time_slots.id"), primary_key=True)

    schedule_entry: Mapped[ScheduleEntry] = relationship(back_populates="slot_links")
    time_slot: Mapped[TimeSlot] = relationship(back_populates="entry_links")


class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (
        CheckConstraint("resource_type IN ('LAB', 'CLASSROOM', 'SEMINAR_HALL', 'AUDITORIUM', 'OTHER')", name="resources_resource_type_check"),
        CheckConstraint("btrim(name) <> ''", name="resources_name_not_blank"),
        CheckConstraint("btrim(normalized_name) <> ''", name="resources_normalized_name_not_blank"),
        CheckConstraint("capacity IS NULL OR capacity > 0", name="resources_capacity_positive"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_name: Mapped[str] = mapped_column(String, nullable=False)
    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    department: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # NULL = shared
    capacity: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    aliases: Mapped[list["ResourceAlias"]] = relationship(back_populates="resource", cascade="all, delete-orphan")
    schedule_entries: Mapped[list[ScheduleEntry]] = relationship(back_populates="resource")


class ResourceAlias(Base):
    __tablename__ = "resource_aliases"
    __table_args__ = (
        CheckConstraint("btrim(alias) <> ''", name="resource_aliases_alias_not_blank"),
        CheckConstraint("btrim(normalized_alias) <> ''", name="resource_aliases_normalized_alias_not_blank"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    resource_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(String, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    resource: Mapped[Resource] = relationship(back_populates="aliases")
