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
| 5 | ElevenLabs call (queue worker, webhook, retries, missed-webhook recovery) | ✅ code + tests; needs your ElevenLabs keys |
| 6 | Summary + compliance check (Claude) | ✅ code + tests; needs `LLM_API_KEY` |
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

## AI calls (ElevenLabs) — milestone 5

How it works: **Confirm** → a `calls` row is queued → the call worker (every 30 s, and immediately on confirm)
starts the call through ElevenLabs → ElevenLabs posts the result to `/api/webhooks/elevenlabs` →
the call becomes **Call done** (patient spoke, transcript saved) or **No answer** (retried after 15 min if it can
still start by 19:50; otherwise handed to the form fallback in milestone 7). A job every 5 minutes asks ElevenLabs
about calls stuck in *Calling* for 15+ minutes, in case a webhook was missed.

Without ElevenLabs keys the app works as before and confirmed calls simply stay **Call queued**.

### One-time setup
1. **Phone number.** Get an Exotel or Plivo number with a SIP trunk, then in ElevenLabs → *Phone Numbers* →
   *Import from SIP trunk*. Copy the phone number id → `ELEVENLABS_PHONE_NUMBER_ID`.
2. **Agent.** ElevenLabs → *Agents* → *New agent*.
   - System prompt: paste `backend/app/prompts/agent_system_prompt.md`.
   - First message: `Hello, this is the AI assistant from {{clinic_name}}, calling about your appointment with {{doctor_name}} at {{appointment_time}}.`
   - Languages: English (default) + Tamil + Hindi. Test Tamil with real speakers.
   - The backend sends these dynamic variables: `patient_name`, `doctor_name`, `clinic_name`,
     `appointment_time`, `language`, `specialty_questions`, `appointment_id`, `call_id`.
   - Copy the agent id → `ELEVENLABS_AGENT_ID`.
3. **API key.** ElevenLabs → *Developers → API keys* → `ELEVENLABS_API_KEY`.
4. **Webhook.** ElevenLabs → *Agents platform settings → Webhooks*: add
   `https://<your-backend>/api/webhooks/elevenlabs`, enable **post-call transcription** and
   **call initiation failure**, and copy the secret → `ELEVENLABS_WEBHOOK_SECRET`.
5. Set `MAX_CONCURRENT_CALLS` to your plan's concurrency limit.

### Testing webhooks on your laptop
ElevenLabs needs a public HTTPS URL. Run a tunnel next to the backend:
```bash
cloudflared tunnel --url http://localhost:8000     # or: ngrok http 8000
```
and use the printed `https://….trycloudflare.com/api/webhooks/elevenlabs` as the webhook URL
(the address changes every time you restart the tunnel).

Calls only start **the day before the visit, until 19:50 IST**, so during the day just book and confirm a slot
for tomorrow. After 19:50 (or 18:00 for booking), shift the clock with `TIME_OFFSET_MINUTES` (see below).

## Summaries (Claude) — milestone 6

When a call completes, a background job sends the transcript to Claude twice:
1. **Summary** → the JSON in `backend/app/prompts/summary_prompt.md` / spec §10 (chief complaint, duration,
   severity, symptoms, medicines, allergies, specialty answers, the patient's questions, …), validated against a
   Pydantic schema. Invalid output is retried once; after that the doctor sees "Summary failed — see transcript".
2. **Compliance check** → did the AI assistant give any medical advice? Flagged calls get a red
   **⚠ Check AI call** badge. If the check itself fails, the call is flagged so a human looks.

The dashboard shows the chief complaint next to each confirmed appointment, a red **⚠ Emergency mentioned**
badge when the patient was told to go to hospital, and the full summary on the appointment page.

Setup: create a key at https://console.anthropic.com → *API keys* and put it in `LLM_API_KEY`.
The model defaults to `claude-opus-5` (`LLM_MODEL` to change). Requests use Anthropic's server-side refusal
fallback, so a safety-classifier decline is retried on a fallback model automatically.
Without a key, calls still complete and the summaries are generated once a key is added.

## Daily timeline (IST, day before the visit)
All in `backend/app/config.py`; every check lives in `backend/app/time_rules.py`.

| Time | What happens |
|---|---|
| 00:05 | Tomorrow's slots generated from the weekly schedule (also on startup) |
| until 18:00 | Patients book tomorrow's slots |
| 18:30 | Doctor gets a reminder email if anything is pending |
| until 19:00 | Doctor confirms / rejects; confirm starts the AI call |
| 19:00 | Remaining pending bookings → `AUTO_CANCELLED`, patient emailed (also catches up on startup) |
| until 19:50 | Calls and retries may start; no call starts after 19:50 |

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
