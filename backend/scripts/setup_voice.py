"""One-time ElevenLabs + Twilio setup (spec §8).

Needs in backend/.env: ELEVENLABS_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN.
Run from backend/:

  uv run python -m scripts.setup_voice check          # verify keys, show Twilio number + verified callers
  uv run python -m scripts.setup_voice create-agent   # create the agent -> ELEVENLABS_AGENT_ID
  uv run python -m scripts.setup_voice import-number  # import Twilio number -> ELEVENLABS_PHONE_NUMBER_ID
  uv run python -m scripts.setup_voice all            # all three, in order
  uv run python -m scripts.setup_voice webhook https://<public-backend-url>
      # (re)create the clinic's post-call webhook -> ELEVENLABS_WEBHOOK_SECRET; restart the backend after


create-agent / import-number / webhook write the new values into backend/.env.
The webhook is attached to the clinic agent only (per-agent override), so any
workspace-wide post-call webhook used by other agents keeps working.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.db import open_pool

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
PROMPTS = Path(__file__).resolve().parents[1] / "app" / "prompts"
EL_API = "https://api.elevenlabs.io/v1/convai"
WEBHOOKS_API = "https://api.elevenlabs.io/v1/workspace/webhooks"
WEBHOOK_NAME = "Clinic AI post-call"
WEBHOOK_EVENTS = ["transcript", "call_initiation_failure"]
TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts"

FIRST_MESSAGE = (
    "Hello, this is the AI assistant from {{clinic_name}}, calling about your appointment "
    "with {{doctor_name}} at {{appointment_time}}."
)
MAX_CALL_SECONDS = 360  # prompt asks for < 5 minutes; hard stop at 6
AGENT_LLM = "gemini-3.6-flash"  # chosen by the clinic: fast + cheap, decent instruction-following

# Sample values so the dashboard's "Test AI agent" works; real calls send their own (calls.dynamic_variables).
PLACEHOLDERS = {
    "patient_name": "Priya",
    "doctor_name": "Dr. Test",
    "clinic_name": "Test Clinic",
    "appointment_time": "10:30 AM tomorrow",
    "language": "en",
    "specialty_questions": "Where on your body is the problem? Is it itchy or painful? Has it spread?",
    "appointment_id": "test",
    "call_id": "test",
}


class SetupError(Exception):
    pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def set_env(key: str, value: str) -> None:
    """Replace KEY=... in backend/.env (or append it)."""
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        text = re.sub(rf"^{key}=.*$", lambda _: line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    ENV_FILE.write_text(text, encoding="utf-8")
    get_settings.cache_clear()
    print(f"  wrote {key} to backend/.env")


def el(method: str, path: str, **kwargs) -> Any:
    key = get_settings().elevenlabs_api_key
    if not key:
        raise SetupError("ELEVENLABS_API_KEY is empty in backend/.env")
    r = httpx.request(method, f"{EL_API}{path}", headers={"xi-api-key": key}, timeout=30, **kwargs)
    if r.status_code >= 400:
        raise SetupError(f"ElevenLabs {method} {path} -> HTTP {r.status_code}: {r.text[:800]}")
    return r.json() if r.content else {}


def twilio(path: str) -> Any:
    s = get_settings()
    if not (s.twilio_account_sid and s.twilio_auth_token):
        raise SetupError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are empty in backend/.env")
    url = f"{TWILIO_API}/{s.twilio_account_sid}{path}"
    r = httpx.get(url, auth=(s.twilio_account_sid, s.twilio_auth_token), timeout=30)
    if r.status_code >= 400:
        raise SetupError(f"Twilio GET {path} -> HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def twilio_numbers() -> list[str]:
    return [n["phone_number"] for n in twilio("/IncomingPhoneNumbers.json").get("incoming_phone_numbers", [])]


def clinic() -> dict[str, Any]:
    """Doctor/clinic row, for naming the agent."""
    with open_pool().connection() as conn:
        row = conn.execute("select name, clinic_name, specialty from doctors order by created_at limit 1").fetchone()
    return row or {"name": "the doctor", "clinic_name": "the clinic", "specialty": ""}


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------

def check() -> None:
    s = get_settings()
    print("ElevenLabs")
    agents = el("GET", "/agents", params={"page_size": 5})
    print(f"  API key OK — {len(agents.get('agents', []))} existing agent(s) visible")
    if s.elevenlabs_agent_id:
        a = el("GET", f"/agents/{s.elevenlabs_agent_id}")
        print(f"  ELEVENLABS_AGENT_ID -> '{a.get('name')}'")
    if s.elevenlabs_phone_number_id:
        p = el("GET", f"/phone-numbers/{s.elevenlabs_phone_number_id}")
        print(f"  ELEVENLABS_PHONE_NUMBER_ID -> {p.get('phone_number')} (agent: {(p.get('assigned_agent') or {}).get('agent_name')})")

    print("Twilio")
    acct = twilio(".json")
    print(f"  Account OK — '{acct.get('friendly_name')}', type {acct.get('type')}, status {acct.get('status')}")
    numbers = twilio_numbers()
    print(f"  Twilio number(s): {', '.join(numbers) or 'NONE — get one in Twilio Console → Phone Numbers'}")
    verified = [c["phone_number"] for c in twilio("/OutgoingCallerIds.json").get("outgoing_caller_ids", [])]
    print(f"  Verified caller IDs: {', '.join(verified) or 'none'}")

    listed = s.verified_numbers
    print("Backend")
    print(f"  TWILIO_TRIAL_MODE={s.twilio_trial_mode}  VERIFIED_TEST_NUMBERS={', '.join(sorted(listed)) or '(empty)'}")
    if acct.get("type") == "Trial" and not s.twilio_trial_mode:
        print("  ! Twilio account is a trial: set TWILIO_TRIAL_MODE=true")
    missing = set(verified) - listed
    extra = listed - set(verified)
    if missing:
        print(f"  ! Verified in Twilio but not in VERIFIED_TEST_NUMBERS: {', '.join(sorted(missing))}")
    if extra:
        print(f"  ! In VERIFIED_TEST_NUMBERS but not verified in Twilio (calls will fail): {', '.join(sorted(extra))}")
    if not (s.elevenlabs_webhook_secret):
        print("  ! ELEVENLABS_WEBHOOK_SECRET is empty — set up the post-call webhook in the dashboard (README)")


def agent_config() -> dict[str, Any]:
    c = clinic()
    system_prompt = (PROMPTS / "agent_system_prompt.md").read_text(encoding="utf-8")
    tool = lambda name: {"type": "system", "name": name, "description": "", "params": {"system_tool_type": name}}  # noqa: E731
    return {
        "name": f"{c['clinic_name']} - intake assistant",
        "tags": ["clinic-ai"],
        "conversation_config": {
            "agent": {
                "language": "en",
                "first_message": FIRST_MESSAGE,
                "dynamic_variables": {"dynamic_variable_placeholders": PLACEHOLDERS},
                "prompt": {
                    "prompt": system_prompt,
                    "llm": AGENT_LLM,
                    "built_in_tools": {
                        "end_call": tool("end_call"),
                        "language_detection": tool("language_detection"),
                    },
                },
            },
            "conversation": {"max_duration_seconds": MAX_CALL_SECONDS},
            # Extra languages; the agent switches with the language_detection tool (spec §9 step 3).
            "language_presets": {"ta": {"overrides": {}}, "hi": {"overrides": {}}},
        },
    }


def create_agent() -> str:
    s = get_settings()
    if s.elevenlabs_agent_id:
        print(f"  ELEVENLABS_AGENT_ID already set ({s.elevenlabs_agent_id}); delete it from .env to create a new one")
        return s.elevenlabs_agent_id
    agent_id = el("POST", "/agents/create", json=agent_config())["agent_id"]
    print(f"  created agent {agent_id}")
    set_env("ELEVENLABS_AGENT_ID", agent_id)
    return agent_id


def import_number() -> str:
    s = get_settings()
    if s.elevenlabs_phone_number_id:
        print(f"  ELEVENLABS_PHONE_NUMBER_ID already set ({s.elevenlabs_phone_number_id})")
        return s.elevenlabs_phone_number_id
    if not s.elevenlabs_agent_id:
        raise SetupError("Create the agent first (create-agent)")
    numbers = twilio_numbers()
    if not numbers:
        raise SetupError("No phone number on the Twilio account — get one in Twilio Console → Phone Numbers")
    number = numbers[0]
    body = {
        "phone_number": number,
        "label": "Clinic AI (Twilio)",
        "sid": s.twilio_account_sid,
        "token": s.twilio_auth_token,
        "provider": "twilio",
        "agent_id": s.elevenlabs_agent_id,
    }
    phone_number_id = el("POST", "/phone-numbers", json=body)["phone_number_id"]
    print(f"  imported {number} -> {phone_number_id}")
    set_env("ELEVENLABS_PHONE_NUMBER_ID", phone_number_id)
    return phone_number_id


def _workspace(method: str, path: str = "", **kwargs) -> Any:
    r = httpx.request(method, f"{WEBHOOKS_API}{path}",
                      headers={"xi-api-key": get_settings().elevenlabs_api_key}, timeout=30, **kwargs)
    if r.status_code >= 400:
        raise SetupError(f"ElevenLabs {method} webhooks{path} -> HTTP {r.status_code}: {r.text[:800]}")
    return r.json() if r.content else {}


def webhook(public_url: str) -> None:
    s = get_settings()
    if not s.elevenlabs_agent_id:
        raise SetupError("Create the agent first (create-agent)")
    url = public_url.rstrip("/") + "/api/webhooks/elevenlabs"
    old = [w for w in _workspace("GET").get("webhooks", []) if w.get("name") == WEBHOOK_NAME]

    # Create + attach the new webhook first: ElevenLabs refuses to delete one that's still in use.
    created = _workspace("POST", json={"settings": {"auth_type": "hmac", "name": WEBHOOK_NAME, "webhook_url": url}})
    webhook_id, secret = created["webhook_id"], created.get("webhook_secret")
    if not secret:
        raise SetupError("ElevenLabs did not return a webhook secret")
    print(f"  created webhook {webhook_id} -> {url}")
    el("PATCH", f"/agents/{s.elevenlabs_agent_id}", json={"platform_settings": {"workspace_overrides": {"webhooks": {
        "post_call_webhook_id": webhook_id, "events": WEBHOOK_EVENTS, "transcript_format": "json", "send_audio": False,
    }}}})
    print("  attached to the clinic agent only")
    set_env("ELEVENLABS_WEBHOOK_SECRET", secret)

    # Now remove our previous webhook(s) (by name); every other workspace webhook is left alone.
    for w in old:
        try:
            _workspace("DELETE", f"/{w['webhook_id']}")
            print(f"  removed old '{WEBHOOK_NAME}' ({w.get('webhook_url')})")
        except SetupError as exc:
            print(f"  could not remove old webhook {w['webhook_id']}: {exc}")
    print("  restart the backend so it picks up the new secret")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("step", choices=["check", "create-agent", "import-number", "all", "webhook"])
    p.add_argument("url", nargs="?", help="public backend URL (webhook step only)")
    args = p.parse_args()
    step = args.step
    try:
        if step == "webhook":
            if not args.url:
                raise SetupError("usage: setup_voice webhook https://<public-backend-url>")
            print("Setting up post-call webhook")
            webhook(args.url)
            return
        if step in ("create-agent", "all"):
            print("Creating agent")
            create_agent()
        if step in ("import-number", "all"):
            print("Importing Twilio number")
            import_number()
        if step in ("check", "all"):
            check()
    except SetupError as exc:
        sys.exit(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
