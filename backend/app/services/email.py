"""Email sending via Resend or SMTP (e.g. Gmail). Logs every attempt in `notifications`.

EMAIL_PROVIDER=resend uses EMAIL_API_KEY; EMAIL_PROVIDER=smtp uses SMTP_* (for Gmail:
smtp.gmail.com:587 with an App Password). With no credentials for the chosen provider,
emails are printed to the console instead (dev mode) and still logged as SENT.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid
from uuid import UUID

import httpx

from app.config import get_settings
from app.db import transaction
from app.services.email_templates import Email

log = logging.getLogger("clinic.email")

RESEND_URL = "https://api.resend.com/emails"


class EmailError(Exception):
    pass


def _configured() -> bool:
    s = get_settings()
    if s.email_provider == "smtp":
        return bool(s.smtp_username and s.smtp_password)
    return bool(s.email_api_key)


def _deliver_smtp(to: str, email: Email) -> str:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.email_from
    msg["To"] = to
    msg["Subject"] = email.subject
    msg["Message-ID"] = make_msgid(domain=s.smtp_username.split("@")[-1] or None)
    msg.set_content(email.text)
    msg.add_alternative(email.html, subtype="html")
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(s.smtp_username, s.smtp_password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailError("SMTP login failed — for Gmail use an App Password, not your normal password") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"SMTP error: {exc}") from exc
    return msg["Message-ID"]


def _deliver(to: str, email: Email) -> str | None:
    """Send the email; return the provider message id. Raises on failure."""
    settings = get_settings()
    if not _configured():
        log.info(
            "[console email] %s -> %s\nSubject: %s\n%s",
            email.type, to, email.subject, email.text,
        )
        return None

    if settings.email_provider == "smtp":
        return _deliver_smtp(to, email)
    if settings.email_provider != "resend":
        raise EmailError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")

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
    if resp.status_code >= 400:
        raise EmailError(f"Resend HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("id")


def send(to: str, email: Email, appointment_id: UUID | str | None = None) -> bool:
    """Send an email and record the outcome. Never raises — email must not break a flow."""
    status, message_id, error = "SENT", None, None
    try:
        message_id = _deliver(to, email)
    except EmailError as exc:
        status, error = "FAILED", str(exc)[:500]
        log.warning("Email %s to %s failed: %s", email.type, to, exc)
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
