# Clinic AI Intake

Patients book tomorrow's appointment, the doctor confirms it, and an AI voice assistant calls the
patient to collect their problem before the visit. Full spec: [clinic-ai-v1-build-spec.md](clinic-ai-v1-build-spec.md).

| Folder | What |
|---|---|
| `backend/` | FastAPI + APScheduler, Postgres via psycopg (Python 3.11+, managed with `uv`) |
| `frontend/` | Next.js (App Router) + Tailwind |
| `supabase/migrations/` | SQL schema |

## Status

| # | Milestone | Status |
|---|---|---|
| 1 | DB + slot generation | ✅ |
| 2 | Booking flow | ✅ |
| 3 | Doctor dashboard | ✅ |
| 4 | Emails + scheduled jobs (18:30 reminder, 19:00 auto-cancel) | ✅ |
| 5 | ElevenLabs call | — (confirm already queues a `calls` row) |
| 6 | Summary + compliance | — (prompts in `backend/app/prompts/`) |
| 7 | Retry + form fallback | — |
| 8 | Hardening (rate limits, retention, Tamil testing) | — |

## Setup

### 1. Supabase
1. **Connection string:** Dashboard → **Connect** → copy the *Session pooler* URI (or *Direct connection* if you have IPv6).
2. **Doctor login:** Dashboard → **Authentication → Users → Add user** (email + password, tick *Auto confirm*).
3. **Keys:** Project Settings → API Keys: Project URL, publishable key (`sb_publishable_…`, or the legacy `anon` key), `service_role` key. If your project uses the legacy JWT secret, copy it too (newer projects use JWKS and don't need it).

### 2. Backend
```bash
cd backend
cp .env.example .env        # fill in DATABASE_URL, SUPABASE_URL, ...
uv sync
uv run python -m scripts.migrate
uv run python -m scripts.seed --email doctor@example.com --name "Dr. Kumar" \
    --clinic "Kumar Skin Clinic" --specialty dermatology \
    --hours 10:00-13:00 --hours 17:00-20:00 --days 0-5
uv run uvicorn app.main:app --reload --port 8000
```
Run **one** backend process: the scheduler runs inside it.

Without `EMAIL_API_KEY`, emails are printed to the backend console (and still logged in `notifications`).
For real email, add a [Resend](https://resend.com) API key and a verified `EMAIL_FROM` domain.

### 3. Frontend
```bash
cd frontend
cp .env.example .env        # fill in the Supabase URL + publishable key
npm install
npm run dev
```
- Patient booking: http://localhost:3000/book
- Doctor dashboard: http://localhost:3000/doctor

## Daily timeline (IST, day before the visit)
All in `backend/app/config.py`; every check lives in `backend/app/time_rules.py`.

| Time | What happens |
|---|---|
| 00:05 | Tomorrow's slots generated from the weekly schedule (also on startup) |
| until 18:00 | Patients book tomorrow's slots |
| 18:30 | Doctor gets a reminder email if anything is pending |
| until 19:00 | Doctor confirms / rejects; confirm queues the AI call |
| 19:00 | Remaining pending bookings → `AUTO_CANCELLED`, patient emailed (also catches up on startup) |

**Testing the deadlines by hand:** set `TIME_OFFSET_MINUTES` in `backend/.env` to shift the app clock
(e.g. the minutes from now until 17:58), then restart the backend. Ignored when `ENVIRONMENT=production`.

## Tests
The tests use a throwaway Postgres in Docker, never your Supabase project.
```bash
cd backend
docker compose -f docker-compose.test.yml up -d
uv run pytest
```
They cover the time boundaries (17:59/18:00, 18:59/19:00, 19:50), double-booking races, confirm vs.
auto-cancel races, the email log, and the HTTP validation.
