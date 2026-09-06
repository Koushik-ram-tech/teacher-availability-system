from sqlalchemy import text
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.teachers import router as teachers_router
from app.core.config import get_settings
from app.db import get_db

settings = get_settings()

app = FastAPI(
    title="Teacher Availability System API",
    version="0.1.0",
    description="Department-level faculty timetable and availability API.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(teachers_router, prefix="/api/v1")


@app.get("/api/v1/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
