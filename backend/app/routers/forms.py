"""Fallback intake form endpoints (spec §7): GET/POST /api/forms/{token}. No login — the token is the key."""

from datetime import date, datetime, time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import forms
from app.services.forms import FormAnswers

router = APIRouter(prefix="/api/forms", tags=["forms"])


class FormInfo(BaseModel):
    patient_name: str
    doctor_name: str
    clinic_name: str
    date: date
    start_time: time
    language: Literal["ta", "en", "hi"]
    specialty_questions: list[str]
    expires_at: datetime


@router.get("/{token}", response_model=FormInfo)
def get_form(token: str):
    return forms.get_form(token)


@router.post("/{token}", status_code=204)
def submit_form(token: str, answers: FormAnswers):
    forms.submit_form(token, answers)
