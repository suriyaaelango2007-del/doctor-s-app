"""Slots, bookings and the doctor's confirm/reject decisions.

All state changes that race with each other (booking the same slot, confirm vs
auto-cancel) take row locks inside a transaction so exactly one wins.
Emails are sent only after the transaction commits.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg import errors

from app import time_rules
from app.db import transaction
from app.services import email, email_templates
from app.services.email_templates import ApptInfo

log = logging.getLogger("clinic.booking")


class BookingError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(BookingError):
    status_code = 404


class Conflict(BookingError):
    status_code = 409


class Closed(BookingError):
    status_code = 422


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

def get_doctor(conn: psycopg.Connection) -> dict[str, Any]:
    """V1 is single-doctor: the clinic's doctor is the only row."""
    doctor = conn.execute("select * from doctors order by created_at limit 1").fetchone()
    if doctor is None:
        raise NotFound("No doctor configured. Run scripts/seed.py.")
    return doctor


def get_doctor_by_auth_user(auth_user_id: str) -> dict[str, Any] | None:
    with transaction() as conn:
        return conn.execute(
            "select * from doctors where auth_user_id = %s", (auth_user_id,)
        ).fetchone()


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------

def generate_slots_for(visit_date: date) -> int:
    """Create slots for `visit_date` from active schedule templates. Idempotent."""
    created = 0
    with transaction() as conn:
        templates = conn.execute(
            """
            select doctor_id, start_time, end_time, slot_minutes
            from schedule_templates
            where weekday = %s and active
            """,
            (visit_date.weekday(),),
        ).fetchall()

        for t in templates:
            step = timedelta(minutes=t["slot_minutes"])
            cursor = datetime.combine(visit_date, t["start_time"])
            end = datetime.combine(visit_date, t["end_time"])
            while cursor + step <= end:
                cur = conn.execute(
                    """
                    insert into slots (doctor_id, date, start_time, end_time)
                    values (%s, %s, %s, %s)
                    on conflict (doctor_id, date, start_time) do nothing
                    """,
                    (t["doctor_id"], visit_date, cursor.time(), (cursor + step).time()),
                )
                created += cur.rowcount
                cursor += step
    return created


def generate_tomorrows_slots() -> int:
    tomorrow = time_rules.tomorrow_ist()
    n = generate_slots_for(tomorrow)
    log.info("Slot generation for %s: %d new slots", tomorrow, n)
    return n


def available_slots(now: datetime | None = None) -> dict[str, Any]:
    """Tomorrow's bookable slots. Empty list once booking has closed."""
    now = now or time_rules.now_ist()
    tomorrow = time_rules.tomorrow_ist(now)
    is_open = time_rules.booking_open(now)
    slots: list[dict[str, Any]] = []
    if is_open:
        with transaction() as conn:
            slots = conn.execute(
                """
                select s.id, s.date, s.start_time, s.end_time
                from slots s
                where s.date = %s
                  and not s.blocked
                  and not exists (
                    select 1 from appointments a
                    where a.slot_id = s.id and a.status in ('PENDING','CONFIRMED')
                  )
                order by s.start_time
                """,
                (tomorrow,),
            ).fetchall()
    return {"date": tomorrow, "booking_open": is_open, "slots": slots}


def set_slot_blocked(slot_id: UUID, doctor_id: UUID, blocked: bool) -> dict[str, Any]:
    with transaction() as conn:
        slot = conn.execute(
            "update slots set blocked = %s where id = %s and doctor_id = %s returning *",
            (blocked, slot_id, doctor_id),
        ).fetchone()
    if slot is None:
        raise NotFound("Slot not found")
    return slot


def list_slots(doctor_id: UUID, slot_date: date) -> list[dict[str, Any]]:
    with transaction() as conn:
        return conn.execute(
            """
            select s.id, s.date, s.start_time, s.end_time, s.blocked,
                   a.id as appointment_id, a.status as appointment_status
            from slots s
            left join appointments a
              on a.slot_id = s.id and a.status in ('PENDING','CONFIRMED')
            where s.doctor_id = %s and s.date = %s
            order by s.start_time
            """,
            (doctor_id, slot_date),
        ).fetchall()


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

@dataclass
class BookingRequest:
    slot_id: UUID
    name: str
    phone: str  # E.164
    email: str
    preferred_language: str
    consent_ai_call: bool


def _appt_info(conn: psycopg.Connection, appointment_id: UUID) -> tuple[ApptInfo, str]:
    """Template data + patient email for an appointment."""
    row = conn.execute(
        """
        select p.name as patient_name, p.email as patient_email,
               s.date, s.start_time, d.name as doctor_name, d.clinic_name
        from appointments a
        join patients p on p.id = a.patient_id
        join slots s on s.id = a.slot_id
        join doctors d on d.id = s.doctor_id
        where a.id = %s
        """,
        (appointment_id,),
    ).fetchone()
    if row is None:
        raise NotFound("Appointment not found")
    info = ApptInfo(
        patient_name=row["patient_name"],
        visit_date=row["date"],
        start_time=row["start_time"],
        doctor_name=row["doctor_name"],
        clinic_name=row["clinic_name"],
    )
    return info, row["patient_email"]


def create_appointment(req: BookingRequest, now: datetime | None = None) -> dict[str, Any]:
    now = now or time_rules.now_ist()

    if not req.consent_ai_call:
        raise BookingError("Consent to the AI assistant call is required.")
    if not time_rules.booking_open(now):
        raise Closed("Booking is closed for tomorrow.")

    try:
        with transaction() as conn:
            # Lock the slot so two bookings for it serialise here.
            slot = conn.execute(
                "select * from slots where id = %s for update", (req.slot_id,)
            ).fetchone()
            if slot is None:
                raise NotFound("Slot not found.")
            if not time_rules.is_bookable_date(slot["date"], now):
                raise BookingError("Only tomorrow's slots can be booked.")
            if slot["blocked"]:
                raise Conflict("This slot is no longer available.")

            patient = conn.execute(
                """
                insert into patients (name, phone, email, preferred_language)
                values (%s, %s, %s, %s)
                on conflict (phone) do update
                  set name = excluded.name,
                      email = excluded.email,
                      preferred_language = excluded.preferred_language
                returning id
                """,
                (req.name, req.phone, req.email, req.preferred_language),
            ).fetchone()

            appt = conn.execute(
                """
                insert into appointments (slot_id, patient_id, consent_ai_call, consent_at)
                values (%s, %s, %s, %s)
                returning id, status, created_at
                """,
                (req.slot_id, patient["id"], req.consent_ai_call, now),
            ).fetchone()

            info, patient_email = _appt_info(conn, appt["id"])
            doctor = get_doctor(conn)
    except errors.UniqueViolation:
        # one_active_booking_per_slot — someone else got there first
        raise Conflict("This slot has just been booked. Please pick another time.") from None

    email.send(patient_email, email_templates.booking_received(info), appt["id"])
    email.send(doctor["email"], email_templates.new_booking(info), appt["id"])

    return {
        "id": appt["id"],
        "status": appt["status"],
        "date": info.visit_date,
        "start_time": info.start_time,
    }


# ---------------------------------------------------------------------------
# Doctor decisions
# ---------------------------------------------------------------------------

def _lock_appointment(conn: psycopg.Connection, appointment_id: UUID, doctor_id: UUID) -> dict[str, Any]:
    row = conn.execute(
        """
        select a.id, a.status, s.date
        from appointments a
        join slots s on s.id = a.slot_id
        where a.id = %s and s.doctor_id = %s
        for update of a
        """,
        (appointment_id, doctor_id),
    ).fetchone()
    if row is None:
        raise NotFound("Appointment not found")
    return row


def _check_decidable(row: dict[str, Any], now: datetime) -> None:
    if row["status"] != "PENDING":
        raise Conflict(f"Appointment is already {row['status'].lower().replace('_', ' ')}.")
    if not time_rules.can_decide(row["date"], now):
        raise Closed("The 7:00 PM decision deadline has passed.")


def confirm(appointment_id: UUID, doctor_id: UUID, now: datetime | None = None) -> dict[str, Any]:
    now = now or time_rules.now_ist()
    with transaction() as conn:
        row = _lock_appointment(conn, appointment_id, doctor_id)
        _check_decidable(row, now)
        conn.execute(
            "update appointments set status = 'CONFIRMED', decided_at = %s where id = %s",
            (now, appointment_id),
        )
        # Queue the AI intake call; the call worker (milestone 5) picks it up.
        conn.execute(
            "insert into calls (appointment_id, status) values (%s, 'QUEUED')",
            (appointment_id,),
        )
        info, patient_email = _appt_info(conn, appointment_id)

    email.send(patient_email, email_templates.confirmed(info), appointment_id)
    return {"id": appointment_id, "status": "CONFIRMED"}


def reject(
    appointment_id: UUID, doctor_id: UUID, reason: str | None = None, now: datetime | None = None
) -> dict[str, Any]:
    now = now or time_rules.now_ist()
    reason = (reason or "").strip() or None
    with transaction() as conn:
        row = _lock_appointment(conn, appointment_id, doctor_id)
        _check_decidable(row, now)
        conn.execute(
            """
            update appointments
            set status = 'REJECTED', decided_at = %s, reject_reason = %s
            where id = %s
            """,
            (now, reason, appointment_id),
        )
        info, patient_email = _appt_info(conn, appointment_id)

    email.send(patient_email, email_templates.rejected(info, reason), appointment_id)
    return {"id": appointment_id, "status": "REJECTED"}


def auto_cancel_pending(now: datetime | None = None) -> int:
    """Cancel every PENDING booking whose 19:00 decision deadline has passed.

    Normally runs at 19:00 for tomorrow's bookings; also catches up anything
    missed (e.g. the server was down at 19:00).
    """
    now = now or time_rules.now_ist()
    tomorrow = time_rules.tomorrow_ist(now)
    # If it's past 19:00 today, tomorrow's bookings are overdue too.
    cutoff = tomorrow if not time_rules.can_decide(tomorrow, now) else time_rules.today_ist(now)

    with transaction() as conn:
        # UPDATE row-locks each match and waits for any in-flight confirm/reject;
        # Postgres then re-checks status = 'PENDING' against the committed row,
        # so exactly one outcome wins.
        rows = conn.execute(
            """
            update appointments a
            set status = 'AUTO_CANCELLED', decided_at = %s
            from slots s
            where s.id = a.slot_id
              and a.status = 'PENDING'
              and s.date <= %s
            returning a.id
            """,
            (now, cutoff),
        ).fetchall()
        infos = [(r["id"], *_appt_info(conn, r["id"])) for r in rows]

    for appt_id, info, patient_email in infos:
        email.send(patient_email, email_templates.auto_cancelled(info), appt_id)

    if infos:
        log.info("Auto-cancelled %d pending bookings (up to %s)", len(infos), cutoff)
    return len(infos)


def send_doctor_reminder(now: datetime | None = None) -> int:
    """18:30 reminder listing tomorrow's PENDING bookings. Returns the count."""
    now = now or time_rules.now_ist()
    tomorrow = time_rules.tomorrow_ist(now)
    with transaction() as conn:
        doctor = get_doctor(conn)
        count = conn.execute(
            """
            select count(*) as n
            from appointments a join slots s on s.id = a.slot_id
            where a.status = 'PENDING' and s.date = %s and s.doctor_id = %s
            """,
            (tomorrow, doctor["id"]),
        ).fetchone()["n"]

    if count:
        email.send(doctor["email"], email_templates.doctor_reminder(count, doctor["clinic_name"]))
    return count


# ---------------------------------------------------------------------------
# Dashboard queries
# ---------------------------------------------------------------------------

_LIST_SQL = """
select a.id, a.status, a.created_at, a.decided_at, a.reject_reason,
       s.id as slot_id, s.date, s.start_time, s.end_time,
       p.name as patient_name, p.phone as patient_phone, p.preferred_language,
       c.status as call_status, c.attempt as call_attempt,
       sm.summary->>'chief_complaint' as summary_preview,
       sm.compliance_flag
from appointments a
join slots s on s.id = a.slot_id
join patients p on p.id = a.patient_id
left join lateral (
  select status, attempt from calls
  where appointment_id = a.id
  order by created_at desc limit 1
) c on true
left join summaries sm on sm.appointment_id = a.id
where s.doctor_id = %(doctor_id)s
"""


def list_appointments(
    doctor_id: UUID, visit_date: date | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    sql = _LIST_SQL
    params: dict[str, Any] = {"doctor_id": doctor_id}
    if visit_date:
        sql += " and s.date = %(date)s"
        params["date"] = visit_date
    if status:
        sql += " and a.status = %(status)s"
        params["status"] = status
    sql += " order by s.date, s.start_time"
    with transaction() as conn:
        return conn.execute(sql, params).fetchall()


def get_appointment_detail(appointment_id: UUID, doctor_id: UUID) -> dict[str, Any]:
    with transaction() as conn:
        appt = conn.execute(
            """
            select a.*, s.date, s.start_time, s.end_time,
                   p.name as patient_name, p.phone as patient_phone,
                   p.email as patient_email, p.preferred_language
            from appointments a
            join slots s on s.id = a.slot_id
            join patients p on p.id = a.patient_id
            where a.id = %s and s.doctor_id = %s
            """,
            (appointment_id, doctor_id),
        ).fetchone()
        if appt is None:
            raise NotFound("Appointment not found")
        calls = conn.execute(
            """
            select id, status, attempt, started_at, ended_at, duration_seconds,
                   transcript, failure_reason, next_retry_at, created_at
            from calls where appointment_id = %s order by created_at
            """,
            (appointment_id,),
        ).fetchall()
        summary = conn.execute(
            "select source, summary, compliance_flag, compliance_notes, created_at "
            "from summaries where appointment_id = %s",
            (appointment_id,),
        ).fetchone()
    appt["calls"] = calls
    appt["summary"] = summary
    appt["decision_deadline"] = time_rules.decision_deadline(appt["date"])
    return appt
