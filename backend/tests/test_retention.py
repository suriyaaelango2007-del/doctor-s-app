"""Spec §14: transcripts / recordings / form answers deleted after RETENTION_DAYS."""

from datetime import timedelta

import pytest
from psycopg.types.json import Jsonb

from app import db
from app.config import get_settings
from app.services import booking, calls, retention
from tests.conftest import TODAY, ist

NOON = ist(TODAY, 12)
LATER = NOON + timedelta(days=31)


@pytest.fixture
def old_call(slots, doctor):
    a = booking.create_appointment(
        booking.BookingRequest(slot_id=slots[0]["id"], name="Mohan", phone="+919876543210", email="m@test.in",
                               preferred_language="en", consent_ai_call=True), now=NOON)
    booking.confirm(a["id"], doctor["id"], now=NOON)
    with db.transaction() as conn:
        conn.execute(
            """update calls set status = 'COMPLETED', elevenlabs_conversation_id = 'conv_1',
               transcript = %s, ended_at = %s where appointment_id = %s""",
            (Jsonb([{"role": "user", "message": "rash"}]), NOON, a["id"]),
        )
        conn.execute("insert into summaries (appointment_id, source, summary) values (%s, 'CALL', %s)",
                     (a["id"], Jsonb({"chief_complaint": "rash"})))
    return a


@pytest.fixture
def deleted(monkeypatch):
    gone = []
    monkeypatch.setattr(calls, "is_configured", lambda: True)
    monkeypatch.setattr(retention, "delete_conversation", lambda cid: gone.append(cid) or True)
    return gone


def row(sql, *args):
    with db.transaction() as conn:
        return conn.execute(sql, args).fetchone()


def test_nothing_purged_before_retention_period(old_call, deleted):
    assert retention.purge_old_data(now=NOON + timedelta(days=29)) == {"calls": 0, "forms": 0}
    assert deleted == []


def test_old_call_purged_but_summary_kept(old_call, deleted):
    assert retention.purge_old_data(now=LATER) == {"calls": 1, "forms": 0}
    assert deleted == ["conv_1"]
    c = row("select transcript, data_purged_at from calls")
    assert c["transcript"] is None and c["data_purged_at"] == LATER
    assert row("select summary from summaries")["summary"] == {"chief_complaint": "rash"}
    # runs once per call
    assert retention.purge_old_data(now=LATER + timedelta(days=1))["calls"] == 0
    assert deleted == ["conv_1"]


def test_remote_delete_failure_keeps_local_copy_for_retry(old_call, monkeypatch):
    monkeypatch.setattr(calls, "is_configured", lambda: True)
    monkeypatch.setattr(retention, "delete_conversation", lambda cid: False)
    assert retention.purge_old_data(now=LATER)["calls"] == 0
    assert row("select transcript from calls")["transcript"] is not None


def test_active_calls_are_never_purged(old_call, deleted):
    with db.transaction() as conn:
        conn.execute("update calls set status = 'CALLING'")
    assert retention.purge_old_data(now=LATER)["calls"] == 0


def test_old_form_answers_cleared(old_call, deleted):
    with db.transaction() as conn:
        conn.execute(
            "insert into intake_forms (appointment_id, token_hash, expires_at, used_at, answers) values (%s,'h',%s,%s,%s)",
            (old_call["id"], NOON, NOON, Jsonb({"main_problem": "rash"})))
    assert retention.purge_old_data(now=LATER)["forms"] == 1
    assert row("select answers from intake_forms")["answers"] is None


def test_retention_days_configurable(old_call, deleted, monkeypatch):
    monkeypatch.setattr(get_settings(), "retention_days", 7)
    assert retention.purge_old_data(now=NOON + timedelta(days=8))["calls"] == 1


def test_delete_conversation_http(monkeypatch):
    class R:
        def __init__(self, code):
            self.status_code = code

    seen = []
    monkeypatch.setattr(retention.httpx, "delete", lambda url, headers, timeout: seen.append(url) or R(404))
    assert retention.delete_conversation("c9") is True  # already gone
    assert seen == ["https://api.elevenlabs.io/v1/convai/conversations/c9"]
    monkeypatch.setattr(retention.httpx, "delete", lambda *a, **k: R(500))
    assert retention.delete_conversation("c9") is False
