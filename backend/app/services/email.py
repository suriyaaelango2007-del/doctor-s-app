"""Email sending via Resend. Logs every attempt in `notifications`.

With EMAIL_API_KEY empty, emails are printed to the console instead (dev mode)
and still logged as SENT so the rest of the flow behaves the same.
"""

import logging
from uuid import UUID

import httpx

from app.config import get_settings
from app.db import transaction
from app.services.email_templates import Email

log = logging.getLogger("clinic.email")

RESEND_URL = "https://api.resend.com/emails"


def _deliver(to: str, email: Email) -> str | None:
    """Send the email; return the provider message id. Raises on failure."""
    settings = get_settings()
    if not settings.email_api_key:
        log.info(
            "[console email] %s -> %s\nSubject: %s\n%s",
            email.type, to, email.subject, email.text,
        )
        return None

    if settings.email_provider != "resend":
        raise RuntimeError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")

    resp = httpx.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {settings.email_api_key}"},
        json={
            "from": settings.email_from,
            "to": [to],
            "subject": email.subject,
            "html": email.html,
            "text": email.text,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("id")


def send(to: str, email: Email, appointment_id: UUID | str | None = None) -> bool:
    """Send an email and record the outcome. Never raises — email must not break a flow."""
    status, message_id, error = "SENT", None, None
    try:
        message_id = _deliver(to, email)
    except Exception as exc:  # noqa: BLE001
        status, error = "FAILED", str(exc)[:500]
        log.exception("Email %s to %s failed", email.type, to)

    try:
        with transaction() as conn:
            conn.execute(
                """
                insert into notifications
                  (appointment_id, type, recipient, status, provider_message_id, error)
                values (%s, %s, %s, %s, %s, %s)
                """,
                (appointment_id, email.type, to, status, message_id, error),
            )
    except Exception:  # noqa: BLE001
        log.exception("Failed to log notification %s", email.type)

    return status == "SENT"
