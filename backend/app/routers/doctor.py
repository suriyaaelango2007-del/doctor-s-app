from datetime import date, datetime, time
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import time_rules
from app.auth import CurrentDoctor
from app.jobs.scheduler import kick_call_worker
from app.services import booking

router = APIRouter(prefix="/api/doctor", tags=["doctor"])

AppointmentStatus = Literal["PENDING", "CONFIRMED", "REJECTED", "AUTO_CANCELLED"]


class AppointmentRow(BaseModel):
    id: UUID
    status: str
    created_at: datetime
    decided_at: datetime | None
    reject_reason: str | None
    slot_id: UUID
    date: date
    start_time: time
    end_time: time
    patient_name: str
    patient_phone: str
    preferred_language: str
    call_status: str | None
    call_attempt: int | None
    summary_preview: str | None
    compliance_flag: bool | None


class Me(BaseModel):
    id: UUID
    name: str
    specialty: str
    clinic_name: str
    email: str
    today: date
    tomorrow: date
    now: datetime
    decision_deadline: datetime


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class BlockIn(BaseModel):
    blocked: bool = True


@router.get("/me", response_model=Me)
def me(doctor: CurrentDoctor):
    now = time_rules.now_ist()
    tomorrow = time_rules.tomorrow_ist(now)
    return {
        **doctor,
        "today": time_rules.today_ist(now),
        "tomorrow": tomorrow,
        "now": now,
        "decision_deadline": time_rules.decision_deadline(tomorrow),
    }


@router.get("/appointments", response_model=list[AppointmentRow])
def list_appointments(
    doctor: CurrentDoctor,
    date: date | None = None,
    status: AppointmentStatus | None = None,
):
    return booking.list_appointments(doctor["id"], date, status)


@router.get("/appointments/{appointment_id}")
def get_appointment(appointment_id: UUID, doctor: CurrentDoctor) -> dict[str, Any]:
    return booking.get_appointment_detail(appointment_id, doctor["id"])


@router.post("/appointments/{appointment_id}/confirm")
def confirm(appointment_id: UUID, doctor: CurrentDoctor):
    result = booking.confirm(appointment_id, doctor["id"])
    kick_call_worker()  # spec: the AI call is queued immediately on confirm
    return result


@router.post("/appointments/{appointment_id}/reject")
def reject(appointment_id: UUID, doctor: CurrentDoctor, body: RejectIn | None = None):
    return booking.reject(appointment_id, doctor["id"], body.reason if body else None)


@router.get("/slots")
def list_slots(doctor: CurrentDoctor, date: date | None = None):
    return booking.list_slots(doctor["id"], date or time_rules.tomorrow_ist())


@router.post("/slots/{slot_id}/block")
def block_slot(slot_id: UUID, doctor: CurrentDoctor, body: BlockIn | None = None):
    return booking.set_slot_blocked(slot_id, doctor["id"], body.blocked if body else True)
