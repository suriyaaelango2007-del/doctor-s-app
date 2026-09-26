from datetime import time
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Daily timeline rules (IST, the day before the visit) — spec §4
# ---------------------------------------------------------------------------
IST = ZoneInfo("Asia/Kolkata")

BOOKING_CUTOFF = time(18, 0)
DOCTOR_REMINDER = time(18, 30)
DECISION_DEADLINE = time(19, 0)
LAST_CALL_START = time(19, 50)
CALL_WINDOW_END = time(20, 0)
RETRY_DELAY_MIN = 15
MAX_CALL_ATTEMPTS = 2

SLOT_GENERATION = time(0, 5)

# Call queue (spec §8)
CALL_WORKER_SECONDS = 30
STUCK_CALL_CHECK_MINUTES = 5
STUCK_CALL_AFTER_MINUTES = 15
WEBHOOK_TOLERANCE_SECONDS = 30 * 60  # matches the ElevenLabs SDK

# Specialty questions injected into the AI agent prompt and the fallback form.
SPECIALTY_QUESTIONS: dict[str, list[str]] = {
    "dermatology": [
        "Where on your body is the problem?",
        "Is it itchy or painful?",
        "Has it spread?",
    ],
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database — direct Postgres connection (Supabase pooler / direct URL)
    database_url: str

    # Supabase (auth verification)
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""

    # ElevenLabs (milestone 5)
    elevenlabs_api_key: str = ""
    elevenlabs_agent_id: str = ""
    elevenlabs_phone_number_id: str = ""
    elevenlabs_webhook_secret: str = ""

    # Summary LLM (milestone 6)
    llm_api_key: str = ""

    # Email — empty EMAIL_API_KEY means "log to console"
    email_provider: str = "resend"
    email_api_key: str = ""
    email_from: str = "Clinic <onboarding@resend.dev>"

    frontend_url: str = "http://localhost:3000"
    max_concurrent_calls: int = 3

    # Dev only: shift the app clock by N minutes to test deadline behaviour.
    environment: str = "development"
    time_offset_minutes: int = 0

    # Disable the in-process scheduler (tests, extra workers)
    scheduler_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
