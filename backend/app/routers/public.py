from datetime import date, time
from typing import Literal
from uuid import UUID

import phonenumbers
from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.db import transaction
from app.ratelimit import BOOKING_LIMITER, limit
from app.services import booking

router = APIRouter(prefix="/api", tags=["public"])


class Slot(BaseModel):
    id: UUID
    date: date
    start_time: time
    end_time: time


class SlotsResponse(BaseModel):
    date: date
    booking_open: bool
    slots: list[Slot]


class BookingIn(BaseModel):
    slot_id: UUID
    name: str = Field(min_length=2, max_length=100)
    phone: str
    email: EmailStr
    preferred_language: Literal["ta", "en", "hi"] = "en"
    consent_ai_call: bool

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Name is too short")
        return v

    @field_validator("phone")
    @classmethod
    def indian_mobile(cls, v: str) -> str:
        """Accept '9876543210', '+91 98765 43210', etc. Store as E.164."""
        try:
            num = phonenumbers.parse(v, "IN")
        except phonenumbers.NumberParseException as exc:
            raise ValueError("Enter a valid 10-digit mobile number") from exc
        mobile_types = (phonenumbers.PhoneNumberType.MOBILE, phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE)
        if (
            num.country_code != 91
            or not phonenumbers.is_valid_number(num)
            or phonenumbers.number_type(num) not in mobile_types
        ):
            raise ValueError("Enter a valid 10-digit Indian mobile number")
        return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


class BookingOut(BaseModel):
    id: UUID
    status: str
    date: date
    start_time: time


class ClinicInfo(BaseModel):
    clinic_name: str
    doctor_name: str
    specialty: str


@router.get("/clinic", response_model=ClinicInfo)
def get_clinic():
    with transaction() as conn:
        d = booking.get_doctor(conn)
    return {"clinic_name": d["clinic_name"], "doctor_name": d["name"], "specialty": d["specialty"]}


@router.get("/slots", response_model=SlotsResponse)
def get_slots():
    return booking.available_slots()


@router.post(
    "/appointments",
    response_model=BookingOut,
    status_code=201,
    dependencies=[Depends(limit(BOOKING_LIMITER, "booking"))],
)
def create_appointment(body: BookingIn):
    return booking.create_appointment(booking.BookingRequest(**body.model_dump()))
