# Backend

FastAPI service for timetable, availability, and file-import workflows.

## Day-1 local setup

From `backend/`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `DATABASE_URL` in `.env` to the Supabase PostgreSQL connection string. Do not commit `.env`.

Then run:

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Health check:

```text
http://localhost:8000/api/v1/health
```

Interactive API docs:

```text
http://localhost:8000/docs
```

The current Day-1 vertical slice provides program lookup, teacher creation/list/search/get/update, and database-backed health checking. Timetable CRUD and availability are implemented next according to `docs/API.md`.
