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
