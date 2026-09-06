from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProgramOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    level: str
    is_active: bool


class TeacherCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    acronym: str = Field(min_length=1, max_length=50)
    level: str
    program_id: UUID
    semester: int = Field(gt=0, le=20)
    department: str = Field(default="Prototype Department", min_length=1, max_length=255)

    @field_validator("name", "acronym", "department")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in {"UG", "PG"}:
            raise ValueError("level must be UG or PG")
        return value


class TeacherUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    acronym: str = Field(min_length=1, max_length=50)
    level: str
    program_id: UUID
    semester: int = Field(gt=0, le=20)
    department: str = Field(min_length=1, max_length=255)
    is_active: bool = True

    @field_validator("name", "acronym", "department")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in {"UG", "PG"}:
            raise ValueError("level must be UG or PG")
        return value


class TeacherOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    acronym: str
    level: str
    program_id: UUID
    semester: int
    department: str
    is_active: bool

    program: ProgramOut
