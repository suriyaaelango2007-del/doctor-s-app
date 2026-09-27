"""Email delivery via Resend: failures are logged with Resend's reason, never raised."""

import json

import pytest

from app import db
from app.config import get_settings
from app.services import email
from app.services.email_templates import Email

MSG = Email(type="CONFIRMED", subject="s", text="t", html="<p>t</p>")


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def resend(monkeypatch):
    monkeypatch.setattr(get_settings(), "email_api_key", "re_test")
    sent = {}

    def respond(status, body):
        def fake_post(url, headers, json, timeout):
            sent.update(url=url, headers=headers, json=json)
            return FakeResponse(status, body)

        monkeypatch.setattr(email.httpx, "post", fake_post)

    return respond, sent


def last_notification():
    with db.transaction() as conn:
        return conn.execute("select type, recipient, status, provider_message_id, error from notifications").fetchone()


def test_resend_success(resend):
    respond, sent = resend
    respond(200, {"id": "msg_1"})
    assert email.send("p@test.in", MSG) is True
    assert sent["url"] == "https://api.resend.com/emails" and sent["json"]["to"] == ["p@test.in"]
    assert sent["headers"] == {"Authorization": "Bearer re_test"}
    assert last_notification() == {"type": "CONFIRMED", "recipient": "p@test.in", "status": "SENT",
                                   "provider_message_id": "msg_1", "error": None}


def test_resend_403_records_reason(resend):
    respond, _ = resend
    respond(403, {"message": "You can only send testing emails to your own email address"})
    assert email.send("p@test.in", MSG) is False
    n = last_notification()
    assert n["status"] == "FAILED"
    assert n["error"].startswith("Resend HTTP 403:") and "own email address" in n["error"]


def test_console_mode_without_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "email_api_key", "")
    assert email.send("p@test.in", MSG) is True
    assert last_notification()["status"] == "SENT"


# ---------------------------------------------------------------------------
# SMTP (Gmail)
# ---------------------------------------------------------------------------

class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail_login = False
    fail_send = False

    def __init__(self, host, port, timeout):
        self.host, self.port, self.calls, self.sent = host, port, [], []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context):
        self.calls.append("starttls")

    def login(self, user, password):
        if FakeSMTP.fail_login:
            raise email.smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        if FakeSMTP.fail_send:
            raise email.smtplib.SMTPRecipientsRefused({"x": (550, b"no")})
        self.sent.append(msg)


@pytest.fixture
def gmail(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "email_provider", "smtp")
    monkeypatch.setattr(s, "smtp_username", "clinic@gmail.com")
    monkeypatch.setattr(s, "smtp_password", "abcd efgh ijkl mnop")
    monkeypatch.setattr(s, "email_from", "Test Clinic <clinic@gmail.com>")
    FakeSMTP.instances, FakeSMTP.fail_login, FakeSMTP.fail_send = [], False, False
    monkeypatch.setattr(email.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def test_smtp_sends_text_and_html(gmail):
    assert email.send("p@test.in", MSG) is True
    smtp = gmail.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 587)
    assert smtp.calls == ["starttls", ("login", "clinic@gmail.com", "abcd efgh ijkl mnop")]
    msg = smtp.sent[0]
    assert msg["To"] == "p@test.in" and msg["From"] == "Test Clinic <clinic@gmail.com>" and msg["Subject"] == "s"
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]
    n = last_notification()
    assert n["status"] == "SENT" and n["provider_message_id"].endswith("@gmail.com>")


def test_smtp_bad_password_is_explained(gmail):
    gmail.fail_login = True
    assert email.send("p@test.in", MSG) is False
    assert "App Password" in last_notification()["error"]


def test_smtp_send_error_recorded(gmail):
    gmail.fail_send = True
    assert email.send("p@test.in", MSG) is False
    assert last_notification()["error"].startswith("SMTP error")


def test_smtp_without_password_uses_console(gmail, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_password", "")
    assert email.send("p@test.in", MSG) is True
    assert gmail.instances == []
