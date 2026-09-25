"""One function per email type (spec §11).

Never put health details in email bodies — link to the dashboard instead.
"""

from dataclasses import dataclass
from datetime import date, time
from html import escape

from app.config import get_settings


@dataclass(frozen=True)
class Email:
    type: str
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class ApptInfo:
    """What templates need to know about an appointment."""

    patient_name: str
    visit_date: date
    start_time: time
    doctor_name: str
    clinic_name: str


def fmt_date(d: date) -> str:
    return d.strftime("%A, %d %B %Y")


def fmt_time(t: time) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def _build(type_: str, subject: str, paragraphs: list[str], link: tuple[str, str] | None = None) -> Email:
    text = "\n\n".join(paragraphs)
    html_parts = [f"<p>{escape(p)}</p>" for p in paragraphs]
    if link:
        label, url = link
        text += f"\n\n{label}: {url}"
        html_parts.append(f'<p><a href="{escape(url, quote=True)}">{escape(label)}</a></p>')
    html = (
        '<div style="font-family:system-ui,sans-serif;font-size:15px;line-height:1.5;color:#111">'
        + "".join(html_parts)
        + "</div>"
    )
    return Email(type=type_, subject=subject, text=text, html=html)


def _when(a: ApptInfo) -> str:
    return f"{fmt_date(a.visit_date)} at {fmt_time(a.start_time)}"


def booking_received(a: ApptInfo) -> Email:
    return _build(
        "BOOKING_RECEIVED",
        f"Appointment request received — {a.clinic_name}",
        [
            f"Hello {a.patient_name},",
            f"We received your appointment request with {a.doctor_name} for {_when(a)}.",
            "This request is waiting for the doctor's confirmation. You'll get another email once the doctor confirms.",
        ],
    )


def new_booking(a: ApptInfo) -> Email:
    s = get_settings()
    return _build(
        "NEW_BOOKING",
        f"New booking request — {fmt_time(a.start_time)} tomorrow",
        [
            f"{a.patient_name} requested an appointment for {_when(a)}.",
            "Please confirm or reject it before 7:00 PM today.",
        ],
        ("Open dashboard", f"{s.frontend_url}/doctor"),
    )


def doctor_reminder(pending_count: int, clinic_name: str) -> Email:
    s = get_settings()
    plural = "request is" if pending_count == 1 else "requests are"
    return _build(
        "DOCTOR_REMINDER",
        f"{pending_count} booking {plural.split()[0]} waiting for you",
        [
            f"{pending_count} booking {plural} still pending for tomorrow at {clinic_name}.",
            "Anything not confirmed by 7:00 PM will be cancelled automatically.",
        ],
        ("Review bookings", f"{s.frontend_url}/doctor"),
    )


def confirmed(a: ApptInfo) -> Email:
    return _build(
        "CONFIRMED",
        f"Appointment confirmed — {_when(a)}",
        [
            f"Hello {a.patient_name},",
            f"Your appointment with {a.doctor_name} at {a.clinic_name} is confirmed for {_when(a)}.",
            "You'll get a call from our AI assistant shortly. It will ask a few questions about your "
            "problem so the doctor is ready for your visit.",
        ],
    )


def rejected(a: ApptInfo, reason: str | None) -> Email:
    s = get_settings()
    paragraphs = [
        f"Hello {a.patient_name},",
        f"Sorry, {a.doctor_name} could not accept your appointment request for {_when(a)}.",
    ]
    if reason:
        paragraphs.append(f"Reason: {reason}")
    paragraphs.append("You're welcome to book another time.")
    return _build(
        "REJECTED",
        f"Appointment request not accepted — {a.clinic_name}",
        paragraphs,
        ("Book again", f"{s.frontend_url}/book"),
    )


def auto_cancelled(a: ApptInfo) -> Email:
    s = get_settings()
    return _build(
        "AUTO_CANCELLED",
        f"Appointment request cancelled — {a.clinic_name}",
        [
            f"Hello {a.patient_name},",
            f"The doctor couldn't confirm your request for {_when(a)} in time, so it has been cancelled.",
            "You can book again for the day after tomorrow.",
        ],
        ("Book again", f"{s.frontend_url}/book"),
    )
