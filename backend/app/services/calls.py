"""ElevenLabs outbound intake calls: queue worker, result handling, recovery.

Lifecycle (spec §5):
  QUEUED -> CALLING -> COMPLETED
                    -> NO_ANSWER / FAILED -> (retry if allowed) -> CALLING
                                          -> (attempts exhausted) -> form fallback (milestone 7)

A call row is claimed (status CALLING, attempt+1) in one transaction and the
ElevenLabs request is made after commit, so a slow API never holds row locks.
"""

import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import psycopg
from psycopg.types.json import Jsonb

from app import config, time_rules
from app.config import get_settings
from app.db import transaction
from app.services.email_templates import fmt_time

log = logging.getLogger("clinic.calls")

API_BASE = "https://api.elevenlabs.io/v1/convai"

# Statuses after which nothing more happens to the call row from ElevenLabs' side.
FINAL_STATUSES = ("COMPLETED", "FORM_SENT", "FORM_SUBMITTED")


class ElevenLabsError(Exception):
    pass


# ---------------------------------------------------------------------------
# ElevenLabs API (thin wrapper so tests can replace it)
# ---------------------------------------------------------------------------

def is_configured() -> bool:
    s = get_settings()
    return bool(s.elevenlabs_api_key and s.elevenlabs_agent_id and s.elevenlabs_phone_number_id)


def _headers() -> dict[str, str]:
    return {"xi-api-key": get_settings().elevenlabs_api_key}


def start_outbound_call(to_number: str, dynamic_variables: dict[str, Any]) -> dict[str, Any]:
    """POST /twilio/outbound-call. Returns {conversation_id, twilio_call_sid}."""
    s = get_settings()
    try:
        resp = httpx.post(
            f"{API_BASE}/twilio/outbound-call",
            headers=_headers(),
            json={
                "agent_id": s.elevenlabs_agent_id,
                "agent_phone_number_id": s.elevenlabs_phone_number_id,
                "to_number": to_number,
                "conversation_initiation_client_data": {"dynamic_variables": dynamic_variables},
                "telephony_call_config": {"ringing_timeout_secs": config.RINGING_TIMEOUT_SECS},
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise ElevenLabsError(f"request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise ElevenLabsError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    if not body.get("success"):
        raise ElevenLabsError(body.get("message") or "outbound call was not accepted")
    return {"conversation_id": body.get("conversation_id"), "twilio_call_sid": body.get("callSid")}


def get_conversation(conversation_id: str) -> dict[str, Any]:
    """GET /conversations/{id} — used when a webhook was missed."""
    try:
        resp = httpx.get(f"{API_BASE}/conversations/{conversation_id}", headers=_headers(), timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ElevenLabsError(f"fetch conversation failed: {exc}") from exc
    return resp.json()


# ---------------------------------------------------------------------------
# Hooks for later milestones
# ---------------------------------------------------------------------------

def on_call_completed(call_id: UUID) -> None:
    """Summarise right away (in the background) instead of waiting for the next worker tick."""
    from app.jobs.scheduler import kick  # late import: the scheduler imports this module

    kick("summary_worker")


def on_attempts_exhausted(call_id: UUID) -> None:
    """Milestone 7: send the fallback intake form."""
    log.info("Call %s out of attempts — form fallback arrives in milestone 7", call_id)


# ---------------------------------------------------------------------------
# Queue worker
# ---------------------------------------------------------------------------

def can_call_number(phone: str) -> bool:
    """On a Twilio trial account only verified test numbers can be called."""
    s = get_settings()
    return not s.twilio_trial_mode or phone in s.verified_numbers


def dynamic_variables(row: dict[str, Any]) -> dict[str, Any]:
    questions = config.SPECIALTY_QUESTIONS.get(row["specialty"].lower(), [])
    return {
        "patient_name": row["patient_name"],
        "doctor_name": row["doctor_name"],
        "clinic_name": row["clinic_name"],
        "appointment_time": f"{fmt_time(row['start_time'])} tomorrow",
        "language": row["preferred_language"],
        "specialty_questions": " ".join(questions) or "None.",
        "appointment_id": str(row["appointment_id"]),
        "call_id": str(row["call_id"]),
    }


_CLAIM_SQL = """
select c.id as call_id, c.attempt, a.id as appointment_id, s.date, s.start_time,
       p.name as patient_name, p.phone, p.preferred_language,
       d.name as doctor_name, d.clinic_name, d.specialty
from calls c
join appointments a on a.id = c.appointment_id
join slots s on s.id = a.slot_id
join patients p on p.id = a.patient_id
join doctors d on d.id = s.doctor_id
where a.status = 'CONFIRMED'
  and s.date = %(tomorrow)s
  and (
    c.status = 'QUEUED'
    or (c.status in ('NO_ANSWER','FAILED') and c.next_retry_at is not null and c.next_retry_at <= %(now)s)
  )
order by c.next_retry_at nulls first, c.created_at
limit %(limit)s
for update of c skip locked
"""


def process_call_queue(now: datetime | None = None) -> int:
    """Start due calls, up to MAX_CONCURRENT_CALLS in flight. Returns calls started."""
    now = now or time_rules.now_ist()
    if not is_configured():
        return 0
    tomorrow = time_rules.tomorrow_ist(now)
    if not time_rules.can_start_call(tomorrow, now):
        return 0

    with transaction() as conn:
        in_flight = conn.execute("select count(*) as n from calls where status = 'CALLING'").fetchone()["n"]
        capacity = get_settings().max_concurrent_calls - in_flight
        if capacity <= 0:
            return 0
        claimed = conn.execute(_CLAIM_SQL, {"tomorrow": tomorrow, "now": now, "limit": capacity}).fetchall()
        rows, not_callable = [], []
        for r in claimed:
            if not can_call_number(r["phone"]):
                # Twilio trial accounts can't call unverified numbers: go straight to the form.
                conn.execute(
                    """
                    update calls set status = 'FAILED', failure_reason = %s, next_retry_at = null, ended_at = %s
                    where id = %s
                    """,
                    ("Twilio trial: number is not a verified test number — sent the intake form instead",
                     now, r["call_id"]),
                )
                not_callable.append(r["call_id"])
                continue
            rows.append(r)
            conn.execute(
                """
                update calls
                set status = 'CALLING', attempt = attempt + 1, started_at = %s,
                    next_retry_at = null, failure_reason = null,
                    ended_at = null, duration_seconds = null
                where id = %s
                """,
                (now, r["call_id"]),
            )

    for call_id in not_callable:
        on_attempts_exhausted(call_id)

    started = 0
    for r in rows:
        try:
            result = start_outbound_call(r["phone"], dynamic_variables(r))
        except ElevenLabsError as exc:
            log.warning("Starting call %s failed: %s", r["call_id"], exc)
            with transaction() as conn:
                exhausted = _record_failure(conn, r["call_id"], "FAILED", str(exc)[:500], now)
            if exhausted:
                on_attempts_exhausted(r["call_id"])
            continue
        with transaction() as conn:
            conn.execute(
                "update calls set elevenlabs_conversation_id = %s, twilio_call_sid = %s where id = %s",
                (result["conversation_id"], result["twilio_call_sid"], r["call_id"]),
            )
        started += 1
        log.info("Call %s started (attempt %d, conversation %s)", r["call_id"], r["attempt"] + 1,
                 result["conversation_id"])
    return started


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

def _lock_call(conn: psycopg.Connection, where: str, value: Any) -> dict[str, Any] | None:
    return conn.execute(
        f"""
        select c.id, c.status, c.attempt, s.date
        from calls c
        join appointments a on a.id = c.appointment_id
        join slots s on s.id = a.slot_id
        where {where} = %s
        for update of c
        """,
        (value,),
    ).fetchone()


def _record_failure(conn: psycopg.Connection, call_id: UUID, status: str, reason: str, now: datetime) -> bool:
    """Mark NO_ANSWER/FAILED and schedule a retry if the rules allow.

    Returns True when no retry is possible; the caller then runs
    on_attempts_exhausted() *after* committing.
    """
    call = _lock_call(conn, "c.id", call_id)
    retry = time_rules.retry_allowed(call["attempt"], call["date"], now)
    next_retry = now + timedelta(minutes=config.RETRY_DELAY_MIN) if retry else None
    conn.execute(
        """
        update calls set status = %s, failure_reason = %s, next_retry_at = %s,
                         ended_at = coalesce(ended_at, %s)
        where id = %s
        """,
        (status, reason, next_retry, now, call_id),
    )
    return not retry


def patient_spoke(transcript: list[dict[str, Any]] | None) -> bool:
    return any(t.get("role") == "user" and (t.get("message") or "").strip() for t in transcript or [])


def _find_call(conn: psycopg.Connection, data: dict[str, Any]) -> dict[str, Any] | None:
    conversation_id = data.get("conversation_id")
    if conversation_id:
        call = _lock_call(conn, "c.elevenlabs_conversation_id", conversation_id)
        if call:
            return call
    dyn = (data.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {}
    call_id = dyn.get("call_id")
    if call_id:
        try:
            return _lock_call(conn, "c.id", UUID(str(call_id)))
        except ValueError:
            return None
    return None


def handle_conversation_result(data: dict[str, Any], now: datetime | None = None) -> str:
    """Apply a finished conversation (post_call_transcription webhook or API fetch).

    Returns what happened: 'completed', 'no_answer', 'ignored' or 'unknown_call'.
    """
    now = now or time_rules.now_ist()
    transcript = data.get("transcript") or []
    meta = data.get("metadata") or {}
    completed = exhausted = False

    with transaction() as conn:
        call = _find_call(conn, data)
        if call is None:
            return "unknown_call"
        if call["status"] != "CALLING":
            return "ignored"  # duplicate delivery, or already handled by the recovery job

        conn.execute(
            """
            update calls
            set transcript = %s, duration_seconds = %s, ended_at = %s,
                elevenlabs_conversation_id = coalesce(elevenlabs_conversation_id, %s)
            where id = %s
            """,
            (Jsonb(transcript), meta.get("call_duration_secs"), now, data.get("conversation_id"), call["id"]),
        )
        if patient_spoke(transcript):
            conn.execute(
                "update calls set status = 'COMPLETED', next_retry_at = null, failure_reason = null where id = %s",
                (call["id"],),
            )
            completed = True
        else:
            reason = meta.get("termination_reason") or "patient did not speak"
            exhausted = _record_failure(conn, call["id"], "NO_ANSWER", str(reason)[:500], now)

    if completed:
        on_call_completed(call["id"])
        return "completed"
    if exhausted:
        on_attempts_exhausted(call["id"])
    return "no_answer"


def handle_initiation_failure(data: dict[str, Any], now: datetime | None = None) -> str:
    """call_initiation_failure webhook: the phone never connected."""
    now = now or time_rules.now_ist()
    reason = data.get("failure_reason") or "unknown"
    status = "NO_ANSWER" if reason in ("busy", "no-answer") else "FAILED"
    with transaction() as conn:
        call = _find_call(conn, data)
        if call is None:
            return "unknown_call"
        if call["status"] != "CALLING":
            return "ignored"
        exhausted = _record_failure(conn, call["id"], status, f"call not connected: {reason}", now)
    if exhausted:
        on_attempts_exhausted(call["id"])
    return "no_answer"


def handle_webhook(event: dict[str, Any], now: datetime | None = None) -> str:
    kind = event.get("type")
    data = event.get("data") or {}
    if kind == "post_call_transcription":
        return handle_conversation_result(data, now)
    if kind == "call_initiation_failure":
        return handle_initiation_failure(data, now)
    return "ignored"  # post_call_audio etc.


# ---------------------------------------------------------------------------
# Safety net for missed webhooks
# ---------------------------------------------------------------------------

def _fail_and_maybe_exhaust(call_id: UUID, reason: str, now: datetime) -> None:
    with transaction() as conn:
        call = _lock_call(conn, "c.id", call_id)
        if call is None or call["status"] != "CALLING":
            return  # a webhook got there first
        exhausted = _record_failure(conn, call_id, "FAILED", reason, now)
    if exhausted:
        on_attempts_exhausted(call_id)


def recover_stuck_calls(now: datetime | None = None) -> int:
    """CALLING rows older than STUCK_CALL_AFTER_MINUTES: ask ElevenLabs what happened."""
    now = now or time_rules.now_ist()
    if not is_configured():
        return 0
    cutoff = now - timedelta(minutes=config.STUCK_CALL_AFTER_MINUTES)
    with transaction() as conn:
        stuck = conn.execute(
            "select id, elevenlabs_conversation_id from calls where status = 'CALLING' and started_at < %s",
            (cutoff,),
        ).fetchall()

    handled = 0
    for c in stuck:
        if not c["elevenlabs_conversation_id"]:
            # The process died between claiming the row and getting a conversation id.
            _fail_and_maybe_exhaust(c["id"], "call start was interrupted", now)
            handled += 1
            continue
        try:
            conv = get_conversation(c["elevenlabs_conversation_id"])
        except ElevenLabsError as exc:
            log.warning("Recovery: %s", exc)
            continue
        if conv.get("status") == "done":
            handle_conversation_result(conv, now)
            handled += 1
        elif conv.get("status") == "failed":
            if patient_spoke(conv.get("transcript")):
                handle_conversation_result(conv, now)
            else:
                _fail_and_maybe_exhaust(c["id"], "conversation failed", now)
            handled += 1
        # initiated / in-progress / processing: leave it for the webhook or the next check
    return handled
