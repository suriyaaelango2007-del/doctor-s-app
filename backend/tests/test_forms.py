"""Milestone 7: fallback intake form."""

from datetime import timedelta
from uuid import uuid4

import pytest
import time_machine
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services import booking, calls, email_templates, forms
from tests.conftest import TODAY, TOMORROW, ist

NOON = ist(TODAY, 12)


def book(slot, name="Mohan", phone="+919876543210", lang="ta"):
    return booking.create_appointment(
        booking.BookingRequest(slot_id=slot["id"], name=name, phone=phone, email=f"{name.lower()}@test.in",
                               preferred_language=lang, consent_ai_call=True),
        now=NOON,
    )


@pytest.fixture
def confirmed(slots, doctor):
    appt = book(slots[0])
    booking.confirm(appt["id"], doctor["id"], now=NOON)
    return appt


@pytest.fixture
def sent_tokens(monkeypatch):
    """Capture raw tokens (they're only ever in the email)."""
    tokens = []
    real = forms.new_token

    def capture():
        t = real()
        tokens.append(t)
        return t

    monkeypatch.setattr(forms, "new_token", capture)
    return tokens


def q(sql, *args):
    with db.transaction() as conn:
        return conn.execute(sql, args).fetchall()


def call_status(appt_id):
    return q("select status from calls where appointment_id = %s", appt_id)[0]["status"]


GOOD = {
    "language": "ta",
    "main_problem": "கையில் அரிப்பு",  # stored exactly as written
    "duration": "2 weeks",
    "severity": "moderate",
    "current_medicines": "Cetirizine, Vitamin D",
    "allergies": "none",
    "past_treatments": "",
    "specialty_answers": {"Where on your body is the problem?": "Left arm", "Unknown?": "x"},
    "patient_questions": "Is it contagious?\nCan I swim?",
}


# ---------------------------------------------------------------------------
# Tokens + sending
# ---------------------------------------------------------------------------

def test_token_is_random_and_only_hash_stored(confirmed, sent_tokens):
    assert forms.send_form(confirmed["id"], now=ist(TODAY, 20)) is True
    token = sent_tokens[0]
    assert len(token) >= 43  # 32 bytes, url-safe base64
    row = q("select token_hash, expires_at from intake_forms")[0]
    assert row["token_hash"] == forms.hash_token(token) != token
    assert row["expires_at"] == ist(TOMORROW, 9, 0)  # clinic opening on the visit day


def test_send_form_emails_short_details_and_sets_form_sent(confirmed, sent_tokens):
    forms.send_form(confirmed["id"], now=ist(TODAY, 20))
    assert call_status(confirmed["id"]) == "FORM_SENT"
    n = q("select type, recipient from notifications where type = 'INTAKE_FORM'")
    assert n == [{"type": "INTAKE_FORM", "recipient": "mohan@test.in"}]


def test_send_form_only_once(confirmed):
    assert forms.send_form(confirmed["id"], now=ist(TODAY, 20)) is True
    assert forms.send_form(confirmed["id"], now=ist(TODAY, 20, 5)) is False
    assert len(q("select id from intake_forms")) == 1


def test_no_form_for_unconfirmed_or_after_expiry(slots, doctor, confirmed):
    pending = book(slots[1], name="Priya")
    assert forms.send_form(pending["id"], now=ist(TODAY, 20)) is False
    assert forms.send_form(confirmed["id"], now=ist(TOMORROW, 9, 0)) is False


def test_no_form_when_summary_exists(confirmed):
    from psycopg.types.json import Jsonb

    with db.transaction() as conn:
        conn.execute("insert into summaries (appointment_id, source, summary) values (%s, 'CALL', %s)",
                     (confirmed["id"], Jsonb({})))
    assert forms.send_form(confirmed["id"], now=ist(TODAY, 20)) is False


def test_attempts_exhausted_hook_sends_form(confirmed):
    call_id = q("select id from calls")[0]["id"]
    with time_machine.travel(ist(TODAY, 18, 30), tick=False):  # before 20:00: sent right away
        calls.on_attempts_exhausted(call_id)
    assert call_status(confirmed["id"]) == "FORM_SENT"
    assert len(q("select id from intake_forms")) == 1


# ---------------------------------------------------------------------------
# 20:00 sweep
# ---------------------------------------------------------------------------

def test_sweep_waits_until_2000(confirmed):
    assert forms.send_pending_forms(now=ist(TODAY, 19, 59)) == 0
    assert forms.send_pending_forms(now=ist(TODAY, 20, 0)) == 1
    assert forms.send_pending_forms(now=ist(TODAY, 20, 5)) == 0  # already sent


def test_sweep_skips_completed_and_in_progress_calls(slots, doctor):
    done, calling, queued = book(slots[0]), book(slots[1], name="Priya"), book(slots[2], name="Ravi")
    for a in (done, calling, queued):
        booking.confirm(a["id"], doctor["id"], now=NOON)
    with db.transaction() as conn:
        conn.execute("update calls set status = 'COMPLETED' where appointment_id = %s", (done["id"],))
        conn.execute("update calls set status = 'CALLING' where appointment_id = %s", (calling["id"],))
    assert forms.send_pending_forms(now=ist(TODAY, 20)) == 1
    assert call_status(queued["id"]) == "FORM_SENT"
    assert call_status(calling["id"]) == "CALLING"


def test_sweep_catches_up_after_midnight_until_clinic_opens(confirmed):
    assert forms.send_pending_forms(now=ist(TOMORROW, 7, 0)) == 1  # server was down at 20:00


def test_sweep_ignores_past_visits(confirmed):
    assert forms.send_pending_forms(now=ist(TOMORROW, 9, 30)) == 0


# ---------------------------------------------------------------------------
# Patient side
# ---------------------------------------------------------------------------

@pytest.fixture
def token(confirmed, sent_tokens):
    forms.send_form(confirmed["id"], now=ist(TODAY, 20))
    return sent_tokens[0]


def test_get_form(token):
    info = forms.get_form(token, now=ist(TODAY, 21))
    assert info["patient_name"] == "Mohan" and info["language"] == "ta"
    assert info["doctor_name"] == "Dr. Kumar" and info["start_time"].hour == 10
    assert info["specialty_questions"][0] == "Where on your body is the problem?"


def test_invalid_expired_and_used_links(token):
    with pytest.raises(booking.NotFound):
        forms.get_form("nope", now=ist(TODAY, 21))
    with pytest.raises(forms.FormGone, match="expired"):
        forms.get_form(token, now=ist(TOMORROW, 9, 0))
    forms.submit_form(token, forms.FormAnswers(**GOOD), now=ist(TODAY, 21))
    with pytest.raises(forms.FormGone, match="already been submitted"):
        forms.get_form(token, now=ist(TODAY, 21, 5))
    with pytest.raises(forms.FormGone):
        forms.submit_form(token, forms.FormAnswers(**GOOD), now=ist(TODAY, 21, 5))


def test_cancelled_appointment_link_is_gone(token, confirmed):
    with db.transaction() as conn:
        conn.execute("update appointments set status = 'REJECTED' where id = %s", (confirmed["id"],))
    with pytest.raises(forms.FormGone, match="no longer active"):
        forms.get_form(token, now=ist(TODAY, 21))


def test_submit_creates_form_summary_as_written(token, confirmed):
    forms.submit_form(token, forms.FormAnswers(**GOOD), now=ist(TODAY, 21))
    sm = q("select source, summary, compliance_flag from summaries")[0]
    assert sm["source"] == "FORM" and sm["compliance_flag"] is False
    s = sm["summary"]
    assert s["chief_complaint"] == "கையில் அரிப்பு"
    assert s["duration"] == "2 weeks" and s["severity"] == "moderate"
    assert s["current_medicines"] == ["Cetirizine", "Vitamin D"]
    assert s["allergies"] == [] and s["past_treatments"] == []
    assert s["specialty_answers"] == {
        "Where on your body is the problem?": "Left arm",
        "Is it itchy or painful?": "not mentioned",
        "Has it spread?": "not mentioned",
    }
    assert s["patient_questions"] == ["Is it contagious?", "Can I swim?"]
    assert s["language"] == "ta" and s["hospital_advice_given"] is False
    assert call_status(confirmed["id"]) == "FORM_SUBMITTED"
    stored = q("select answers, used_at from intake_forms")[0]
    assert stored["used_at"] == ist(TODAY, 21)
    assert "Unknown?" not in stored["answers"]["specialty_answers"]


def test_to_list():
    assert forms.to_list("A, B\nC; D") == ["A", "B", "C", "D"]
    assert forms.to_list("None") == [] and forms.to_list("  ") == []


@pytest.mark.parametrize("none", ["இல்லை", "எதுவும் இல்லை", "नहीं", "कोई नहीं", "नहीं।", "None.", "NIL"])
def test_to_list_none_in_all_languages(none):
    assert forms.to_list(none) == []


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(app)


def test_api_form_flow(client, token):
    with time_machine.travel(ist(TODAY, 21), tick=False):
        r = client.get(f"/api/forms/{token}")
        assert r.status_code == 200 and r.json()["patient_name"] == "Mohan"
        assert client.post(f"/api/forms/{token}", json=GOOD).status_code == 204
        assert client.get(f"/api/forms/{token}").status_code == 410
        assert client.post(f"/api/forms/{token}", json=GOOD).status_code == 410
    assert client.get("/api/forms/not-a-token").status_code == 404


@pytest.mark.parametrize("patch", [{"main_problem": ""}, {"severity": "terrible"}, {"language": "fr"},
                                   {"main_problem": "x" * 2001}])
def test_api_form_validation(client, token, patch):
    with time_machine.travel(ist(TODAY, 21), tick=False):
        assert client.post(f"/api/forms/{token}", json={**GOOD, **patch}).status_code == 422


# ---------------------------------------------------------------------------
# Emails: short, with name, date & time and phone
# ---------------------------------------------------------------------------

def test_confirmation_and_form_emails_are_short(confirmed):
    with db.transaction() as conn:
        info, _ = booking.appointment_info(conn, confirmed["id"])
    for e in (email_templates.confirmed(info), email_templates.intake_form(info, "http://x/form/t", "9:00 AM")):
        assert "Name: Mohan" in e.text
        assert "Date & time: Thursday, 24 September 2026 at 10:00 AM" in e.text
        assert "Phone: +91 98765 43210" in e.text
        assert len(e.text) < 400
    assert "AI assistant" in email_templates.confirmed(info).text
    assert "http://x/form/t" in email_templates.intake_form(info, "http://x/form/t", "9:00 AM").text


def test_link_expiry_timezone_is_ist(confirmed, sent_tokens):
    forms.send_form(confirmed["id"], now=ist(TODAY, 20))
    expires = q("select expires_at from intake_forms")[0]["expires_at"]
    assert expires - ist(TODAY, 20) == timedelta(hours=13)
