"""Milestone 5: call queue, ElevenLabs webhooks, missed-webhook recovery.

The ElevenLabs API is replaced by a fake; no real calls are made.
"""

import hashlib
import hmac
import json
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import app
from app.routers.webhooks import verify_signature
from app.services import booking, calls
from tests.conftest import TODAY, TOMORROW, ist

NOON = ist(TODAY, 12)
SECRET = "whsec_test_secret"


class FakeElevenLabs:
    def __init__(self):
        self.started: list[dict] = []
        self.fail_start = False
        self.conversations: dict[str, dict] = {}
        self.exhausted: list = []
        self.completed: list = []

    def start_outbound_call(self, to_number, dynamic_variables):
        if self.fail_start:
            raise calls.ElevenLabsError("HTTP 500: boom")
        conv = f"conv_{len(self.started) + 1}"
        self.started.append({"to": to_number, "vars": dynamic_variables, "conversation_id": conv})
        return {"conversation_id": conv, "sip_call_id": f"sip_{len(self.started)}"}

    def get_conversation(self, conversation_id):
        return self.conversations[conversation_id]


@pytest.fixture
def el(monkeypatch):
    fake = FakeElevenLabs()
    monkeypatch.setattr(calls, "is_configured", lambda: True)
    monkeypatch.setattr(calls, "start_outbound_call", fake.start_outbound_call)
    monkeypatch.setattr(calls, "get_conversation", fake.get_conversation)
    monkeypatch.setattr(calls, "on_attempts_exhausted", fake.exhausted.append)
    monkeypatch.setattr(calls, "on_call_completed", fake.completed.append)
    monkeypatch.setattr(get_settings(), "max_concurrent_calls", 3)
    return fake


def book(slot, phone="+919876543210", name="Priya"):
    return booking.BookingRequest(
        slot_id=slot["id"], name=name, phone=phone, email="p@test.in",
        preferred_language="ta", consent_ai_call=True,
    )


@pytest.fixture
def confirmed(slots, doctor):
    """One confirmed appointment for tomorrow 10:00 with a QUEUED call."""
    appt = booking.create_appointment(book(slots[0]), now=NOON)
    booking.confirm(appt["id"], doctor["id"], now=NOON)
    return appt


def call_row(appt_id):
    with db.transaction() as conn:
        return conn.execute(
            "select * from calls where appointment_id = %s order by created_at desc limit 1", (appt_id,)
        ).fetchone()


def transcription(conversation_id, user_says="I have a rash on my arm", duration=95, **data_over):
    transcript = [{"role": "agent", "message": "Hello, this is the AI assistant...", "time_in_call_secs": 0}]
    if user_says is not None:
        transcript.append({"role": "user", "message": user_says, "time_in_call_secs": 5})
    data = {
        "agent_id": "agent_1", "conversation_id": conversation_id, "status": "done",
        "transcript": transcript,
        "metadata": {"call_duration_secs": duration, "termination_reason": "call ended by remote party"},
    }
    data.update(data_over)
    return {"type": "post_call_transcription", "event_timestamp": int(time.time()), "data": data}


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def test_worker_starts_queued_call(el, confirmed):
    assert calls.process_call_queue(now=ist(TODAY, 18, 58)) == 1
    c = call_row(confirmed["id"])
    assert (c["status"], c["attempt"], c["elevenlabs_conversation_id"], c["sip_call_id"]) == (
        "CALLING", 1, "conv_1", "sip_1")
    assert c["started_at"] == ist(TODAY, 18, 58)

    started = el.started[0]
    assert started["to"] == "+919876543210"
    v = started["vars"]
    assert v["patient_name"] == "Priya" and v["doctor_name"] == "Dr. Kumar"
    assert v["clinic_name"] == "Kumar Skin Clinic" and v["language"] == "ta"
    assert v["appointment_time"] == "10:00 AM tomorrow"
    assert v["appointment_id"] == str(confirmed["id"]) and v["call_id"] == str(c["id"])
    assert "Where on your body" in v["specialty_questions"]


def test_worker_never_starts_after_1950(el, confirmed):
    assert calls.process_call_queue(now=ist(TODAY, 19, 50, 1)) == 0
    assert calls.process_call_queue(now=ist(TOMORROW, 9, 0)) == 0
    assert call_row(confirmed["id"])["status"] == "QUEUED"
    assert calls.process_call_queue(now=ist(TODAY, 19, 50)) == 1


def test_worker_does_nothing_when_not_configured(el, confirmed, monkeypatch):
    monkeypatch.setattr(calls, "is_configured", lambda: False)
    assert calls.process_call_queue(now=NOON) == 0
    assert call_row(confirmed["id"])["status"] == "QUEUED"


def test_worker_respects_concurrency_limit(el, slots, doctor, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_concurrent_calls", 2)
    for i, s in enumerate(slots[:3]):
        a = booking.create_appointment(book(s, phone=f"+91987654321{i}"), now=NOON)
        booking.confirm(a["id"], doctor["id"], now=NOON)
    assert calls.process_call_queue(now=NOON) == 2
    assert calls.process_call_queue(now=NOON) == 0  # both lines busy
    assert len(el.started) == 2


def test_worker_skips_pending_and_rejected(el, slots, doctor):
    booking.create_appointment(book(slots[0]), now=NOON)  # pending: no call row at all
    a = booking.create_appointment(book(slots[1], phone="+919812345678"), now=NOON)
    booking.reject(a["id"], doctor["id"], now=NOON)
    assert calls.process_call_queue(now=NOON) == 0


def test_start_failure_schedules_retry(el, confirmed):
    el.fail_start = True
    now = ist(TODAY, 18, 0)
    assert calls.process_call_queue(now=now) == 0
    c = call_row(confirmed["id"])
    assert c["status"] == "FAILED" and c["attempt"] == 1
    assert "HTTP 500" in c["failure_reason"]
    assert c["next_retry_at"] == now + timedelta(minutes=15)
    assert el.exhausted == []


def test_retry_is_picked_up_after_delay(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    calls.handle_webhook(transcription("conv_1", user_says=None), now=ist(TODAY, 18, 2))
    assert call_row(confirmed["id"])["status"] == "NO_ANSWER"

    assert calls.process_call_queue(now=ist(TODAY, 18, 16)) == 0  # retry due 18:17
    assert calls.process_call_queue(now=ist(TODAY, 18, 17)) == 1
    c = call_row(confirmed["id"])
    assert (c["status"], c["attempt"], c["elevenlabs_conversation_id"]) == ("CALLING", 2, "conv_2")
    assert c["next_retry_at"] is None


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

def test_completed_call_saves_transcript(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    assert calls.handle_webhook(transcription("conv_1"), now=ist(TODAY, 18, 3)) == "completed"
    c = call_row(confirmed["id"])
    assert c["status"] == "COMPLETED" and c["duration_seconds"] == 95
    assert c["transcript"][1] == {"role": "user", "message": "I have a rash on my arm", "time_in_call_secs": 5}
    assert c["ended_at"] == ist(TODAY, 18, 3)
    assert el.completed == [c["id"]]


def test_no_patient_speech_is_no_answer(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    assert calls.handle_webhook(transcription("conv_1", user_says="   "), now=ist(TODAY, 18, 1)) == "no_answer"
    c = call_row(confirmed["id"])
    assert c["status"] == "NO_ANSWER"
    assert c["failure_reason"] == "call ended by remote party"
    assert c["next_retry_at"] == ist(TODAY, 18, 16)


def test_confirm_at_1858_no_retry_past_1950(el, slots, doctor):
    """Spec §16: confirm at 18:58 -> call starts; retry not scheduled past 19:50 -> form instead."""
    appt = booking.create_appointment(book(slots[0]), now=NOON)
    booking.confirm(appt["id"], doctor["id"], now=ist(TODAY, 18, 58))
    calls.process_call_queue(now=ist(TODAY, 18, 58))
    calls.handle_webhook(transcription("conv_1", user_says=None), now=ist(TODAY, 19, 1))
    calls.process_call_queue(now=ist(TODAY, 19, 16))  # retry at 19:16
    # second attempt unanswered: out of attempts -> form fallback
    calls.handle_webhook(transcription("conv_2", user_says=None), now=ist(TODAY, 19, 18))
    c = call_row(appt["id"])
    assert (c["status"], c["attempt"], c["next_retry_at"]) == ("NO_ANSWER", 2, None)
    assert el.exhausted == [c["id"]]


def test_late_no_answer_goes_straight_to_form(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 19, 40))
    calls.handle_webhook(transcription("conv_1", user_says=None), now=ist(TODAY, 19, 42))
    c = call_row(confirmed["id"])
    assert (c["attempt"], c["next_retry_at"]) == (1, None)  # 19:57 retry would be too late
    assert el.exhausted == [c["id"]]


def test_duplicate_webhook_is_ignored(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    assert calls.handle_webhook(transcription("conv_1"), now=ist(TODAY, 18, 3)) == "completed"
    assert calls.handle_webhook(transcription("conv_1", user_says=None), now=ist(TODAY, 18, 4)) == "ignored"
    assert call_row(confirmed["id"])["status"] == "COMPLETED"
    assert len(el.completed) == 1


def test_webhook_matched_by_call_id_when_conversation_unknown(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    c = call_row(confirmed["id"])
    event = transcription(
        "conv_other",
        conversation_initiation_client_data={"dynamic_variables": {"call_id": str(c["id"])}},
    )
    with db.transaction() as conn:  # simulate: webhook raced ahead of saving the conversation id
        conn.execute("update calls set elevenlabs_conversation_id = null where id = %s", (c["id"],))
    assert calls.handle_webhook(event, now=ist(TODAY, 18, 3)) == "completed"
    assert call_row(confirmed["id"])["elevenlabs_conversation_id"] == "conv_other"


def test_unknown_conversation(el):
    assert calls.handle_webhook(transcription("nope"), now=NOON) == "unknown_call"


@pytest.mark.parametrize("reason, status", [("busy", "NO_ANSWER"), ("no-answer", "NO_ANSWER"), ("unknown", "FAILED")])
def test_call_initiation_failure(el, confirmed, reason, status):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    event = {"type": "call_initiation_failure", "data": {
        "agent_id": "agent_1", "conversation_id": "conv_1", "failure_reason": reason,
        "metadata": {"type": "sip", "body": {"sip_status_code": 486}},
    }}
    calls.handle_webhook(event, now=ist(TODAY, 18, 1))
    c = call_row(confirmed["id"])
    assert c["status"] == status and reason in c["failure_reason"]
    assert c["next_retry_at"] == ist(TODAY, 18, 16)


def test_audio_webhook_ignored(el):
    assert calls.handle_webhook({"type": "post_call_audio", "data": {"conversation_id": "x"}}) == "ignored"


# ---------------------------------------------------------------------------
# Missed-webhook recovery
# ---------------------------------------------------------------------------

def test_recovery_applies_finished_conversation(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    el.conversations["conv_1"] = transcription("conv_1")["data"]
    assert calls.recover_stuck_calls(now=ist(TODAY, 18, 14)) == 0  # not stuck yet
    assert calls.recover_stuck_calls(now=ist(TODAY, 18, 16)) == 1
    assert call_row(confirmed["id"])["status"] == "COMPLETED"


def test_recovery_leaves_in_progress_calls(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    el.conversations["conv_1"] = {"conversation_id": "conv_1", "status": "in-progress"}
    assert calls.recover_stuck_calls(now=ist(TODAY, 18, 20)) == 0
    assert call_row(confirmed["id"])["status"] == "CALLING"


def test_recovery_failed_conversation(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    el.conversations["conv_1"] = {"conversation_id": "conv_1", "status": "failed", "transcript": []}
    assert calls.recover_stuck_calls(now=ist(TODAY, 18, 20)) == 1
    c = call_row(confirmed["id"])
    assert c["status"] == "FAILED" and c["next_retry_at"] == ist(TODAY, 18, 35)


def test_recovery_of_interrupted_start(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    with db.transaction() as conn:
        conn.execute("update calls set elevenlabs_conversation_id = null")
    assert calls.recover_stuck_calls(now=ist(TODAY, 18, 20)) == 1
    assert call_row(confirmed["id"])["failure_reason"] == "call start was interrupted"


# ---------------------------------------------------------------------------
# Webhook HTTP endpoint + signatures
# ---------------------------------------------------------------------------

def sign(body: bytes, ts: int | None = None, secret: str = SECRET) -> str:
    ts = int(time.time()) if ts is None else ts
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v0={mac}"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "elevenlabs_webhook_secret", SECRET)
    return TestClient(app)


def post(client, event, signature=None):
    body = json.dumps(event).encode()
    headers = {"content-type": "application/json"}
    if signature is not False:
        headers["elevenlabs-signature"] = signature or sign(body)
    return client.post("/api/webhooks/elevenlabs", content=body, headers=headers)


def test_webhook_endpoint_completes_call(el, confirmed, client):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    r = post(client, transcription("conv_1"))
    assert r.status_code == 200 and r.json()["outcome"] == "completed"
    assert call_row(confirmed["id"])["status"] == "COMPLETED"


def test_webhook_rejects_missing_or_bad_signature(el, confirmed, client):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    event = transcription("conv_1")
    assert post(client, event, signature=False).status_code == 401
    assert post(client, event, signature=sign(b"other body")).status_code == 401
    assert post(client, event, signature=sign(json.dumps(event).encode(), secret="wrong")).status_code == 401
    assert post(client, event, signature="garbage").status_code == 401
    assert call_row(confirmed["id"])["status"] == "CALLING"


def test_webhook_rejects_old_timestamp(client):
    event = transcription("conv_1")
    old = sign(json.dumps(event).encode(), ts=int(time.time()) - 31 * 60)
    assert post(client, event, signature=old).status_code == 401


def test_webhook_503_without_secret(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "elevenlabs_webhook_secret", "")
    assert post(client, transcription("x")).status_code == 503


def test_webhook_unknown_call_still_200(client):
    r = post(client, transcription(str(uuid4())))
    assert r.status_code == 200 and r.json()["outcome"] == "unknown_call"


def test_verify_signature_tolerance_boundary():
    body = b"{}"
    now = 1_800_000_000
    verify_signature(body, sign(body, ts=now - 1800), SECRET, now=now)  # exactly 30 min: ok
    with pytest.raises(Exception):
        verify_signature(body, sign(body, ts=now - 1801), SECRET, now=now)


# ---------------------------------------------------------------------------
# ElevenLabs HTTP wrapper (request shape per docs, error handling)
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)


@pytest.fixture
def el_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "elevenlabs_api_key", "xi_key")
    monkeypatch.setattr(s, "elevenlabs_agent_id", "agent_123")
    monkeypatch.setattr(s, "elevenlabs_phone_number_id", "phnum_456")
    return s


def test_outbound_call_request_shape(el_settings, monkeypatch):
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, json=json)
        return FakeResponse(200, {"success": True, "message": "ok", "conversation_id": "c1", "sip_call_id": "s1"})

    monkeypatch.setattr(calls.httpx, "post", fake_post)
    assert calls.is_configured()
    result = calls.start_outbound_call("+919876543210", {"patient_name": "Priya"})
    assert result == {"conversation_id": "c1", "sip_call_id": "s1"}
    assert sent["url"] == "https://api.elevenlabs.io/v1/convai/sip-trunk/outbound-call"
    assert sent["headers"] == {"xi-api-key": "xi_key"}
    assert sent["json"] == {
        "agent_id": "agent_123",
        "agent_phone_number_id": "phnum_456",
        "to_number": "+919876543210",
        "conversation_initiation_client_data": {"dynamic_variables": {"patient_name": "Priya"}},
    }


@pytest.mark.parametrize(
    "status, body",
    [(200, {"success": False, "message": "trunk down"}), (422, {"detail": "bad"}), (500, {})],
)
def test_outbound_call_errors(el_settings, monkeypatch, status, body):
    monkeypatch.setattr(calls.httpx, "post", lambda *a, **k: FakeResponse(status, body))
    with pytest.raises(calls.ElevenLabsError):
        calls.start_outbound_call("+919876543210", {})


def test_outbound_call_network_error(el_settings, monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(calls.httpx, "post", boom)
    with pytest.raises(calls.ElevenLabsError, match="request failed"):
        calls.start_outbound_call("+919876543210", {})


def test_get_conversation(el_settings, monkeypatch):
    seen = {}

    def fake_get(url, headers, timeout):
        seen["url"] = url
        return FakeResponse(200, {"status": "done"})

    monkeypatch.setattr(calls.httpx, "get", fake_get)
    assert calls.get_conversation("c1") == {"status": "done"}
    assert seen["url"] == "https://api.elevenlabs.io/v1/convai/conversations/c1"


def test_not_configured_without_keys(monkeypatch):
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", "")
    assert not calls.is_configured()


# ---------------------------------------------------------------------------
# Gaps found by mutation testing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing", ["elevenlabs_api_key", "elevenlabs_agent_id", "elevenlabs_phone_number_id"])
def test_partial_config_is_not_configured(el_settings, monkeypatch, missing):
    assert calls.is_configured()
    monkeypatch.setattr(el_settings, missing, "")
    assert not calls.is_configured()


def test_late_start_failure_hands_off_to_form(el, confirmed):
    el.fail_start = True
    calls.process_call_queue(now=ist(TODAY, 19, 40))
    c = call_row(confirmed["id"])
    assert (c["status"], c["next_retry_at"]) == ("FAILED", None)
    assert el.exhausted == [c["id"]]


def test_initiation_failure_outcomes_and_late_handoff(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 19, 40))
    event = {"type": "call_initiation_failure", "data": {"conversation_id": "conv_1", "failure_reason": "busy"}}
    assert calls.handle_webhook(event, now=ist(TODAY, 19, 41)) == "no_answer"
    c = call_row(confirmed["id"])
    assert c["next_retry_at"] is None and el.exhausted == [c["id"]]
    assert calls.handle_webhook(event, now=ist(TODAY, 19, 42)) == "ignored"  # duplicate
    unknown = {"type": "call_initiation_failure", "data": {"conversation_id": "zzz", "failure_reason": "busy"}}
    assert calls.handle_webhook(unknown) == "unknown_call"


def test_recovery_skips_when_elevenlabs_api_errors(el, confirmed, monkeypatch):
    calls.process_call_queue(now=ist(TODAY, 18, 0))

    def down(_):
        raise calls.ElevenLabsError("503")

    monkeypatch.setattr(calls, "get_conversation", down)
    assert calls.recover_stuck_calls(now=ist(TODAY, 18, 20)) == 0
    assert call_row(confirmed["id"])["status"] == "CALLING"


def test_recovery_failed_conversation_where_patient_spoke(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    data = transcription("conv_1")["data"] | {"status": "failed"}
    el.conversations["conv_1"] = data
    assert calls.recover_stuck_calls(now=ist(TODAY, 18, 20)) == 1
    assert call_row(confirmed["id"])["status"] == "COMPLETED"


def test_recovery_late_interrupted_start_hands_off_to_form(el, confirmed):
    calls.process_call_queue(now=ist(TODAY, 19, 30))
    with db.transaction() as conn:
        conn.execute("update calls set elevenlabs_conversation_id = null")
    calls.recover_stuck_calls(now=ist(TODAY, 19, 46))
    c = call_row(confirmed["id"])
    assert (c["status"], c["next_retry_at"]) == ("FAILED", None)
    assert el.exhausted == [c["id"]]


def test_recovery_does_not_overwrite_a_webhook_result(el, confirmed):
    """Race: the webhook completes the call just before recovery marks it failed."""
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    calls.handle_webhook(transcription("conv_1"), now=ist(TODAY, 18, 3))
    c = call_row(confirmed["id"])
    calls._fail_and_maybe_exhaust(c["id"], "conversation failed", ist(TODAY, 18, 20))
    assert call_row(confirmed["id"])["status"] == "COMPLETED"
    assert el.exhausted == []


def test_recovery_noop_when_not_configured(el, confirmed, monkeypatch):
    calls.process_call_queue(now=ist(TODAY, 18, 0))
    monkeypatch.setattr(calls, "is_configured", lambda: False)
    assert calls.recover_stuck_calls(now=ist(TODAY, 18, 20)) == 0


def test_get_conversation_http_error(el_settings, monkeypatch):
    monkeypatch.setattr(calls.httpx, "get", lambda *a, **k: FakeResponse(404, {"detail": "not found"}))
    with pytest.raises(calls.ElevenLabsError, match="fetch conversation failed"):
        calls.get_conversation("c1")


def test_outbound_call_http_error_even_if_body_claims_success(el_settings, monkeypatch):
    body = {"success": True, "conversation_id": "c1", "sip_call_id": "s1"}
    monkeypatch.setattr(calls.httpx, "post", lambda *a, **k: FakeResponse(400, body))
    with pytest.raises(calls.ElevenLabsError, match="HTTP 400"):
        calls.start_outbound_call("+919876543210", {})


def test_outbound_call_rejection_without_message(el_settings, monkeypatch):
    monkeypatch.setattr(calls.httpx, "post", lambda *a, **k: FakeResponse(200, {"success": False}))
    with pytest.raises(calls.ElevenLabsError, match="not accepted"):
        calls.start_outbound_call("+919876543210", {})


def test_webhook_signed_but_invalid_json(client):
    body = b"{not json"
    r = client.post("/api/webhooks/elevenlabs", content=body, headers={"elevenlabs-signature": sign(body)})
    assert r.status_code == 400
