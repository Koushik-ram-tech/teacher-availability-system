from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve backend/.env by an absolute path derived from this file's location,
# so DATABASE_URL loads correctly no matter what directory `uvicorn` is
# launched from (repo root, backend/, an IDE run config, etc.).
# This file lives at backend/app/core/config.py, so parents[2] == backend/.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    database_url: str
    cors_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()