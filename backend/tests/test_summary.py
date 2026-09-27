"""Milestone 6: transcript -> summary JSON + compliance check (Claude, faked)."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import db
from app.auth import current_doctor
from app.config import get_settings
from app.main import app
from app.services import booking, calls, summary
from app.services.summary import ComplianceResult, IntakeSummary, LLMError, LLMUnavailable
from tests.conftest import TODAY, TOMORROW, ist

NOON = ist(TODAY, 12)

TRANSCRIPT = [
    {"role": "agent", "message": "Hello, this is the AI assistant.", "time_in_call_secs": 0},
    {"role": "user", "message": "I have an itchy rash on my arm for two weeks.", "time_in_call_secs": 6},
    {"role": "agent", "message": "", "time_in_call_secs": 9},
    {"role": "user", "message": "Can I use coconut oil?", "time_in_call_secs": 20},
    {"role": "agent", "message": "I'm not able to advise on that.", "time_in_call_secs": 22},
]


def good_summary(**over) -> IntakeSummary:
    data = dict(
        chief_complaint="Itchy rash on arm", duration="2 weeks", severity="moderate",
        symptoms=["itching", "rash"], current_medicines=[], allergies=["penicillin"],
        past_treatments=[], patient_questions=["Can I use coconut oil?"],
        specialty_answers=[{"question": "Is it itchy or painful?", "answer": "Itchy"}],
        hospital_advice_given=False, language="en", notes="",
    )
    data.update(over)
    return IntakeSummary(**data)


class FakeLLM:
    """Stands in for summary.llm_parse. Queue results per schema; exceptions are raised."""

    def __init__(self):
        self.queue = {IntakeSummary: [], ComplianceResult: []}
        self.calls = []

    def __call__(self, schema, system, content):
        self.calls.append((schema, system, content))
        result = self.queue[schema].pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(summary, "llm_parse", fake)
    monkeypatch.setattr(summary, "is_configured", lambda: True)
    return fake


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def test_format_transcript_labels_speakers_and_skips_empty():
    assert summary.format_transcript(TRANSCRIPT) == (
        "AI assistant: Hello, this is the AI assistant.\n"
        "Patient: I have an itchy rash on my arm for two weeks.\n"
        "Patient: Can I use coconut oil?\n"
        "AI assistant: I'm not able to advise on that."
    )
    assert summary.format_transcript(None) == ""


def test_summary_request_includes_specialty_questions():
    text = summary.summary_request("Patient: hi", "ta", "Dermatology")
    assert "Where on your body is the problem?" in text
    assert "language when booking: ta" in text
    assert "<transcript>\nPatient: hi\n</transcript>" in text


# ---------------------------------------------------------------------------
# Generation + retry rules
# ---------------------------------------------------------------------------

def test_generate_summary_maps_specialty_answers(llm):
    llm.queue[IntakeSummary] = [good_summary()]
    result = summary.generate_summary("Patient: rash", "en", "dermatology")
    assert result["specialty_answers"] == {"Is it itchy or painful?": "Itchy"}
    assert result["chief_complaint"] == "Itchy rash on arm"
    schema, system, content = llm.calls[0]
    assert schema is IntakeSummary
    assert "Use ONLY what the patient said" in system


def test_generate_summary_retries_once(llm):
    llm.queue[IntakeSummary] = [LLMError("bad json"), good_summary()]
    assert summary.generate_summary("x", "en", "dermatology")["chief_complaint"] == "Itchy rash on arm"
    assert len(llm.calls) == 2


def test_generate_summary_gives_up_after_two_failures(llm):
    llm.queue[IntakeSummary] = [LLMError("bad"), LLMError("bad again")]
    result = summary.generate_summary("x", "ta", "dermatology")
    assert result["notes"] == summary.SUMMARY_FAILED_NOTE
    assert result["language"] == "ta" and result["symptoms"] == []
    assert len(llm.calls) == 2


def test_unavailable_is_not_swallowed(llm):
    llm.queue[IntakeSummary] = [LLMUnavailable("down")]
    with pytest.raises(LLMUnavailable):
        summary.generate_summary("x", "en", "dermatology")


def test_compliance_result_passthrough(llm):
    llm.queue[ComplianceResult] = [ComplianceResult(flag=False, notes="")]
    assert summary.check_compliance("x") == ComplianceResult(flag=False, notes="")
    assert "NOT allowed to give medical advice" in llm.calls[0][1]


def test_failed_compliance_check_is_flagged(llm):
    llm.queue[ComplianceResult] = [LLMError("refused")]
    result = summary.check_compliance("x")
    assert result.flag is True and "review the transcript" in result.notes


def test_failed_summary_defaults_unknown_language():
    assert summary.failed_summary("fr")["language"] == "en"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

@pytest.fixture
def completed_call(slots, doctor):
    appt = booking.create_appointment(
        booking.BookingRequest(slot_id=slots[0]["id"], name="Priya", phone="+919876543210",
                               email="p@test.in", preferred_language="en", consent_ai_call=True),
        now=NOON,
    )
    booking.confirm(appt["id"], doctor["id"], now=NOON)
    from psycopg.types.json import Jsonb

    with db.transaction() as conn:
        conn.execute(
            "update calls set status = 'COMPLETED', transcript = %s, ended_at = %s, attempt = 1 "
            "where appointment_id = %s",
            (Jsonb(TRANSCRIPT), NOON, appt["id"]),
        )
    return appt


def summary_row(appt_id):
    with db.transaction() as conn:
        return conn.execute("select * from summaries where appointment_id = %s", (appt_id,)).fetchone()


def test_worker_stores_summary_and_compliance(llm, completed_call):
    llm.queue[IntakeSummary] = [good_summary()]
    llm.queue[ComplianceResult] = [ComplianceResult(flag=True, notes="Assistant named a medicine")]
    assert summary.process_pending_summaries() == 1
    row = summary_row(completed_call["id"])
    assert row["source"] == "CALL"
    assert row["summary"]["chief_complaint"] == "Itchy rash on arm"
    assert row["summary"]["specialty_answers"] == {"Is it itchy or painful?": "Itchy"}
    assert (row["compliance_flag"], row["compliance_notes"]) == (True, "Assistant named a medicine")
    # the prompt carried the transcript text
    assert "Patient: I have an itchy rash" in llm.calls[0][2]


def test_worker_is_idempotent(llm, completed_call):
    llm.queue[IntakeSummary] = [good_summary()]
    llm.queue[ComplianceResult] = [ComplianceResult(flag=False, notes="")]
    assert summary.process_pending_summaries() == 1
    assert summary.process_pending_summaries() == 0  # nothing left; queue would raise if called
    assert summary_row(completed_call["id"])["compliance_notes"] is None


def test_worker_postpones_when_api_unavailable(llm, completed_call):
    llm.queue[IntakeSummary] = [LLMUnavailable("rate limited")]
    assert summary.process_pending_summaries() == 0
    assert summary_row(completed_call["id"]) is None
    # next run succeeds
    llm.queue[IntakeSummary] = [good_summary()]
    llm.queue[ComplianceResult] = [ComplianceResult(flag=False, notes="")]
    assert summary.process_pending_summaries() == 1


def test_worker_stores_failed_placeholder(llm, completed_call):
    llm.queue[IntakeSummary] = [LLMError("x"), LLMError("y")]
    llm.queue[ComplianceResult] = [ComplianceResult(flag=False, notes="")]
    assert summary.process_pending_summaries() == 1
    assert summary_row(completed_call["id"])["summary"]["notes"] == summary.SUMMARY_FAILED_NOTE


def test_worker_skips_calls_that_are_not_completed(llm, completed_call):
    with db.transaction() as conn:
        conn.execute("update calls set status = 'NO_ANSWER'")
    assert summary.process_pending_summaries() == 0


def test_worker_does_nothing_without_api_key(llm, completed_call, monkeypatch):
    monkeypatch.setattr(summary, "is_configured", lambda: False)
    assert summary.process_pending_summaries() == 0


def test_is_configured_follows_api_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_api_key", "")
    assert not summary.is_configured()
    monkeypatch.setattr(get_settings(), "llm_api_key", "sk-ant-test")
    assert summary.is_configured()


def test_dashboard_shows_summary_preview_and_flags(llm, completed_call, doctor):
    llm.queue[IntakeSummary] = [good_summary(hospital_advice_given=True)]
    llm.queue[ComplianceResult] = [ComplianceResult(flag=True, notes="x")]
    summary.process_pending_summaries()
    app.dependency_overrides[current_doctor] = lambda: doctor
    try:
        rows = TestClient(app).get("/api/doctor/appointments", params={"date": str(TOMORROW)}).json()
    finally:
        app.dependency_overrides.clear()
    assert rows[0]["summary_preview"] == "Itchy rash on arm"
    assert rows[0]["compliance_flag"] is True and rows[0]["hospital_advice_given"] is True


def test_completed_webhook_kicks_summary_worker(monkeypatch, completed_call):
    kicked = []
    monkeypatch.setattr("app.jobs.scheduler.kick", kicked.append)
    calls.on_call_completed(completed_call["id"])
    assert kicked == ["summary_worker"]


# ---------------------------------------------------------------------------
# llm_parse: request shape and error mapping (fake Anthropic client)
# ---------------------------------------------------------------------------

class FakeMessages:
    def __init__(self, result):
        self.result, self.kwargs = result, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fake_client(monkeypatch, result):
    messages = FakeMessages(result)
    monkeypatch.setattr(summary, "_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    return messages


def response(parsed=None, stop_reason="end_turn", category=None):
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason,
                           stop_details=SimpleNamespace(category=category))


def test_llm_parse_request_shape(monkeypatch):
    msgs = fake_client(monkeypatch, response(parsed=ComplianceResult(flag=False, notes="")))
    result = summary.llm_parse(ComplianceResult, "SYSTEM", "CONTENT")
    assert result == ComplianceResult(flag=False, notes="")
    k = msgs.kwargs
    assert k["model"] == "claude-opus-5"
    assert k["system"] == "SYSTEM"
    assert k["messages"] == [{"role": "user", "content": "CONTENT"}]
    assert k["output_format"] is ComplianceResult
    assert k["betas"] == ["server-side-fallback-2026-07-01"] and k["fallbacks"] == "default"
    assert k["max_tokens"] == 16000


def test_llm_parse_uses_configured_model(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_model", "claude-fable-5-1")
    msgs = fake_client(monkeypatch, response(parsed=ComplianceResult(flag=False, notes="")))
    summary.llm_parse(ComplianceResult, "s", "c")
    assert msgs.kwargs["model"] == "claude-fable-5-1"
    assert msgs.kwargs["fallbacks"] == "default"


@pytest.mark.parametrize(
    "resp, match",
    [
        (response(stop_reason="refusal", category="bio"), "declined \\(category: bio\\)"),
        (response(stop_reason="max_tokens"), "cut off"),
        (response(parsed=None), "No structured output"),
    ],
)
def test_llm_parse_bad_responses(monkeypatch, resp, match):
    fake_client(monkeypatch, resp)
    with pytest.raises(LLMError, match=match):
        summary.llm_parse(ComplianceResult, "s", "c")


def _status_error(cls, code):
    r = httpx2.Response(code, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls("err", response=r, body=None)


@pytest.mark.parametrize(
    "exc, expected",
    [
        (_status_error(anthropic.AuthenticationError, 401), LLMUnavailable),
        (_status_error(anthropic.PermissionDeniedError, 403), LLMUnavailable),
        (_status_error(anthropic.NotFoundError, 404), LLMUnavailable),
        (_status_error(anthropic.RateLimitError, 429), LLMUnavailable),
        (_status_error(anthropic.InternalServerError, 500), LLMUnavailable),
        (_status_error(anthropic.APIStatusError, 529), LLMUnavailable),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), LLMUnavailable),
        (_status_error(anthropic.BadRequestError, 400), LLMError),
        (_status_error(anthropic.APIStatusError, 413), LLMError),
    ],
)
def test_llm_parse_error_mapping(monkeypatch, exc, expected):
    fake_client(monkeypatch, exc)
    with pytest.raises(expected):
        summary.llm_parse(ComplianceResult, "s", "c")


def test_llm_parse_schema_mismatch_is_llm_error(monkeypatch):
    try:
        ComplianceResult(flag="maybe", notes=1)  # type: ignore[arg-type]
    except ValidationError as exc:
        fake_client(monkeypatch, exc)
    with pytest.raises(LLMError, match="did not match"):
        summary.llm_parse(ComplianceResult, "s", "c")


def test_prompts_exist():
    assert "ONLY valid JSON" in summary._prompt("summary_prompt.md")
    assert "flag" in summary._prompt("compliance_prompt.md")


def test_llm_parse_haiku_has_no_fallback(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_model", "claude-haiku-4-5")
    msgs = fake_client(monkeypatch, response(parsed=ComplianceResult(flag=False, notes="")))
    summary.llm_parse(ComplianceResult, "s", "c")
    assert msgs.kwargs["model"] == "claude-haiku-4-5"
    assert "betas" not in msgs.kwargs and "fallbacks" not in msgs.kwargs
    assert msgs.kwargs["output_format"] is ComplianceResult
