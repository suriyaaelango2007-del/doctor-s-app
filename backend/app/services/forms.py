"""Fallback intake form (spec §5, §10, §14).

Sent when the AI call can't reach the patient: right after the last call
attempt fails, or by the 20:00 sweep for any confirmed appointment still
without a completed call. The link carries a random 32-byte token; only its
SHA-256 is stored. It works once and expires at clinic opening on the visit day.

Answers are stored as written (no translation) and mapped straight into the
§10 summary schema with source = FORM.
"""

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from psycopg import errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import config, time_rules
from app.config import get_settings
from app.db import transaction
from app.services import email, email_templates
from app.services.booking import BookingError, Closed, NotFound, appointment_info

log = logging.getLogger("clinic.forms")


class FormGone(BookingError):
    """Link already used or expired."""

    status_code = 410


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def new_token() -> str:
    return secrets.token_urlsafe(32)  # 32 random bytes


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def form_link(token: str) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}/form/{token}"


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send_form(appointment_id: UUID, now: datetime | None = None) -> bool:
    """Create the form link and email it. Idempotent: returns False if not sent."""
    now = now or time_rules.now_ist()
    token = new_token()
    try:
        with transaction() as conn:
            appt = conn.execute(
                """
                select a.id, a.status, s.date, d.clinic_open_time,
                       exists(select 1 from summaries sm where sm.appointment_id = a.id) as has_summary
                from appointments a
                join slots s on s.id = a.slot_id
                join doctors d on d.id = s.doctor_id
                where a.id = %s
                for update of a
                """,
                (appointment_id,),
            ).fetchone()
            if appt is None or appt["status"] != "CONFIRMED" or appt["has_summary"]:
                return False
            expires_at = time_rules.form_expiry(appt["date"], appt["clinic_open_time"])
            if expires_at <= now:
                return False
            cur = conn.execute(
                """
                insert into intake_forms (appointment_id, token_hash, expires_at)
                values (%s, %s, %s)
                on conflict (appointment_id) do nothing
                """,
                (appointment_id, hash_token(token), expires_at),
            )
            if cur.rowcount == 0:
                return False  # a form was already sent
            conn.execute(
                """
                update calls set status = 'FORM_SENT', next_retry_at = null
                where appointment_id = %s and status <> 'COMPLETED'
                """,
                (appointment_id,),
            )
            info, patient_email = appointment_info(conn, appointment_id)
    except errors.UniqueViolation:
        return False

    expires = f"{email_templates.fmt_time(expires_at.timetz())} on {expires_at.strftime('%A, %d %B')}"
    email.send(patient_email, email_templates.intake_form(info, form_link(token), expires), appointment_id)
    log.info("Intake form sent for appointment %s", appointment_id)
    return True


def send_form_for_call(call_id: UUID) -> bool:
    """Hook for calls.on_attempts_exhausted."""
    with transaction() as conn:
        row = conn.execute("select appointment_id from calls where id = %s", (call_id,)).fetchone()
    return bool(row) and send_form(row["appointment_id"])


def send_pending_forms(now: datetime | None = None) -> int:
    """20:00 sweep (+ startup catch-up): confirmed appointments with no completed call get the form.

    Skips calls still in progress; their outcome triggers the form if needed.
    """
    now = now or time_rules.now_ist()
    today = time_rules.today_ist(now)
    with transaction() as conn:
        rows = conn.execute(
            """
            select a.id, s.date, d.clinic_open_time
            from appointments a
            join slots s on s.id = a.slot_id
            join doctors d on d.id = s.doctor_id
            where a.status = 'CONFIRMED'
              and s.date in (%s, %s)
              and not exists (select 1 from intake_forms f where f.appointment_id = a.id)
              and not exists (select 1 from summaries sm where sm.appointment_id = a.id)
              and not exists (
                select 1 from calls c
                where c.appointment_id = a.id and c.status in ('COMPLETED', 'CALLING')
              )
            """,
            (today, time_rules.tomorrow_ist(now)),
        ).fetchall()
    sent = 0
    for r in rows:
        window_closed = now >= time_rules.at(r["date"] - timedelta(days=1), config.CALL_WINDOW_END)
        if window_closed and now < time_rules.form_expiry(r["date"], r["clinic_open_time"]):
            sent += send_form(r["id"], now)
    return sent


# ---------------------------------------------------------------------------
# Patient side
# ---------------------------------------------------------------------------

def _load(conn, token: str, now: datetime, lock: bool = False) -> dict[str, Any]:
    row = conn.execute(
        f"""
        select f.id as form_id, f.appointment_id, f.expires_at, f.used_at,
               a.status, a.patient_name, a.preferred_language,
               s.date, s.start_time, d.name as doctor_name, d.clinic_name, d.specialty
        from intake_forms f
        join appointments a on a.id = f.appointment_id
        join slots s on s.id = a.slot_id
        join doctors d on d.id = s.doctor_id
        where f.token_hash = %s
        {"for update of f" if lock else ""}
        """,
        (hash_token(token),),
    ).fetchone()
    if row is None:
        raise NotFound("This form link is not valid.")
    if row["used_at"] is not None:
        raise FormGone("This form has already been submitted. Thank you!")
    if now >= row["expires_at"]:
        raise FormGone("This form link has expired.")
    if row["status"] != "CONFIRMED":
        raise FormGone("This appointment is no longer active.")
    return row


def specialty_questions(specialty: str) -> list[str]:
    return config.SPECIALTY_QUESTIONS.get(specialty.lower(), [])


def get_form(token: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or time_rules.now_ist()
    with transaction() as conn:
        row = _load(conn, token, now)
    return {
        "patient_name": row["patient_name"],
        "doctor_name": row["doctor_name"],
        "clinic_name": row["clinic_name"],
        "date": row["date"],
        "start_time": row["start_time"],
        "language": row["preferred_language"],
        "specialty_questions": specialty_questions(row["specialty"]),
        "expires_at": row["expires_at"],
    }


class FormAnswers(BaseModel):
    language: Literal["ta", "en", "hi"]
    main_problem: str = Field(min_length=2, max_length=2000)
    duration: str = Field(default="", max_length=200)
    severity: Literal["mild", "moderate", "severe"]
    current_medicines: str = Field(default="", max_length=1000)
    allergies: str = Field(default="", max_length=1000)
    past_treatments: str = Field(default="", max_length=1000)
    specialty_answers: dict[str, str] = Field(default_factory=dict)
    patient_questions: str = Field(default="", max_length=2000)


# "None" in English, Tamil and Hindi (the form's hint asks patients to write these).
_NONE_WORDS = {
    "no", "none", "nil", "na", "n/a", "-", "nothing", "no allergies", "no medicines",
    "இல்லை", "எதுவும் இல்லை", "ஒன்றும் இல்லை",
    "नहीं", "कोई नहीं", "कुछ नहीं", "नही",
}


def to_list(text: str) -> list[str]:
    """Split a free-text answer on commas / new lines. 'none' (en/ta/hi) / empty -> []."""
    items = [x.strip() for x in re.split(r"[,\n;]+", text or "")]
    return [x for x in items if x and x.casefold().rstrip(".। ") not in _NONE_WORDS]


def answers_to_summary(a: FormAnswers, questions: list[str]) -> dict[str, Any]:
    """Map form answers directly into the §10 summary schema (no LLM, no translation)."""
    return {
        "chief_complaint": a.main_problem.strip(),
        "duration": a.duration.strip() or "not mentioned",
        "severity": a.severity,
        "symptoms": [],
        "current_medicines": to_list(a.current_medicines),
        "allergies": to_list(a.allergies),
        "past_treatments": to_list(a.past_treatments),
        "specialty_answers": {q: (a.specialty_answers.get(q) or "").strip() or "not mentioned" for q in questions},
        "patient_questions": [q.strip() for q in (a.patient_questions or "").split("\n") if q.strip()],
        "hospital_advice_given": False,
        "language": a.language,
        "notes": "",
    }


def submit_form(token: str, answers: FormAnswers, now: datetime | None = None) -> None:
    now = now or time_rules.now_ist()
    with transaction() as conn:
        row = _load(conn, token, now, lock=True)
        questions = specialty_questions(row["specialty"])
        clean = answers.model_copy(update={
            "specialty_answers": {q: answers.specialty_answers.get(q, "") for q in questions},
        })
        conn.execute(
            "update intake_forms set used_at = %s, answers = %s where id = %s",
            (now, Jsonb(clean.model_dump()), row["form_id"]),
        )
        conn.execute(
            "update calls set status = 'FORM_SUBMITTED' where appointment_id = %s and status <> 'COMPLETED'",
            (row["appointment_id"],),
        )
        cur = conn.execute(
            """
            insert into summaries (appointment_id, source, summary, compliance_flag)
            values (%s, 'FORM', %s, false)
            on conflict (appointment_id) do nothing
            """,
            (row["appointment_id"], Jsonb(answers_to_summary(clean, questions))),
        )
        if cur.rowcount == 0:
            raise Closed("A summary already exists for this appointment.")
    log.info("Intake form submitted for appointment %s", row["appointment_id"])
