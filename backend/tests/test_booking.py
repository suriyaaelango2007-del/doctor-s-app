import threading
from uuid import uuid4

import psycopg
import pytest
import time_machine
from fastapi.testclient import TestClient

from app import db
from app.auth import current_doctor
from app.main import app
from app.services import booking
from tests.conftest import TEST_DB, TODAY, TOMORROW, ist

NOON = ist(TODAY, 12)


def req(slot_id, phone="+919876543210", **kw):
    data = dict(
        slot_id=slot_id, name="Priya", phone=phone, email="priya@test.in",
        preferred_language="ta", consent_ai_call=True,
    )
    data.update(kw)
    return booking.BookingRequest(**data)


def status_of(appt_id):
    with db.transaction() as conn:
        return conn.execute("select status from appointments where id = %s", (appt_id,)).fetchone()["status"]


def notifications(appt_id=None):
    with db.transaction() as conn:
        if appt_id is None:
            return [r["type"] for r in conn.execute("select type from notifications order by sent_at")]
        return [
            r["type"]
            for r in conn.execute(
                "select type from notifications where appointment_id = %s order by sent_at", (appt_id,)
            )
        ]


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------

def test_slot_generation_is_idempotent(doctor):
    assert booking.generate_slots_for(TOMORROW) == 4
    assert booking.generate_slots_for(TOMORROW) == 0


def test_available_slots_excludes_booked_and_blocked(slots, doctor):
    booking.create_appointment(req(slots[0]["id"]), now=NOON)
    booking.set_slot_blocked(slots[1]["id"], doctor["id"], True)
    with time_machine.travel(NOON, tick=False):
        result = booking.available_slots()
    assert result["booking_open"]
    assert [s["id"] for s in result["slots"]] == [slots[2]["id"], slots[3]["id"]]


def test_available_slots_empty_after_1800(slots):
    result = booking.available_slots(now=ist(TODAY, 18, 0))
    assert result == {"date": TOMORROW, "booking_open": False, "slots": []}


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

def test_booking_at_1759_succeeds_and_emails(slots):
    appt = booking.create_appointment(req(slots[0]["id"]), now=ist(TODAY, 17, 59))
    assert appt["status"] == "PENDING"
    assert notifications(appt["id"]) == ["BOOKING_RECEIVED", "NEW_BOOKING"]


def test_booking_at_1800_fails(slots):
    with pytest.raises(booking.Closed):
        booking.create_appointment(req(slots[0]["id"]), now=ist(TODAY, 18, 0))


def test_booking_requires_consent(slots):
    with pytest.raises(booking.BookingError):
        booking.create_appointment(req(slots[0]["id"], consent_ai_call=False), now=NOON)


def test_cannot_book_non_tomorrow_slot(doctor):
    booking.generate_slots_for(TODAY)
    with db.transaction() as conn:
        today_slot = conn.execute("select id from slots where date = %s limit 1", (TODAY,)).fetchone()
    with pytest.raises(booking.BookingError):
        booking.create_appointment(req(today_slot["id"]), now=NOON)


def test_cannot_book_blocked_slot(slots, doctor):
    booking.set_slot_blocked(slots[0]["id"], doctor["id"], True)
    with pytest.raises(booking.Conflict):
        booking.create_appointment(req(slots[0]["id"]), now=NOON)


def test_double_booking_blocked(slots):
    booking.create_appointment(req(slots[0]["id"]), now=NOON)
    with pytest.raises(booking.Conflict):
        booking.create_appointment(req(slots[0]["id"], phone="+919812345678"), now=NOON)


def test_slot_reusable_after_reject(slots, doctor):
    appt = booking.create_appointment(req(slots[0]["id"]), now=NOON)
    booking.reject(appt["id"], doctor["id"], "Away", now=NOON)
    again = booking.create_appointment(req(slots[0]["id"], phone="+919812345678"), now=NOON)
    assert again["status"] == "PENDING"


def test_patient_upserted_by_phone(slots):
    booking.create_appointment(req(slots[0]["id"]), now=NOON)
    booking.create_appointment(req(slots[1]["id"], name="Priya K", email="new@test.in"), now=NOON)
    with db.transaction() as conn:
        rows = conn.execute("select name, email from patients").fetchall()
    assert rows == [{"name": "Priya K", "email": "new@test.in"}]


def test_concurrent_booking_same_slot_only_one_wins(slots):
    barrier = threading.Barrier(2)
    results: list[str] = []

    def book(phone):
        barrier.wait()
        try:
            booking.create_appointment(req(slots[0]["id"], phone=phone), now=NOON)
            results.append("ok")
        except booking.Conflict:
            results.append("conflict")

    threads = [threading.Thread(target=book, args=(p,)) for p in ("+919876543210", "+919812345678")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict", "ok"]


# ---------------------------------------------------------------------------
# Doctor decisions
# ---------------------------------------------------------------------------

def test_confirm_at_1859_queues_call_and_emails(slots, doctor):
    appt = booking.create_appointment(req(slots[0]["id"]), now=NOON)
    booking.confirm(appt["id"], doctor["id"], now=ist(TODAY, 18, 59))
    assert status_of(appt["id"]) == "CONFIRMED"
    with db.transaction() as conn:
        calls = conn.execute("select status from calls where appointment_id = %s", (appt["id"],)).fetchall()
    assert calls == [{"status": "QUEUED"}]
    assert notifications(appt["id"])[-1] == "CONFIRMED"


def test_confirm_at_1900_fails(slots, doctor):
    appt = booking.create_appointment(req(slots[0]["id"]), now=NOON)
    with pytest.raises(booking.Closed):
        booking.confirm(appt["id"], doctor["id"], now=ist(TODAY, 19, 0))
    assert status_of(appt["id"]) == "PENDING"


def test_cannot_decide_twice(slots, doctor):
    appt = booking.create_appointment(req(slots[0]["id"]), now=NOON)
    booking.confirm(appt["id"], doctor["id"], now=NOON)
    with pytest.raises(booking.Conflict):
        booking.reject(appt["id"], doctor["id"], now=NOON)


def test_reject_stores_reason_and_emails(slots, doctor):
    appt = booking.create_appointment(req(slots[0]["id"]), now=NOON)
    booking.reject(appt["id"], doctor["id"], "  Clinic closed  ", now=NOON)
    with db.transaction() as conn:
        row = conn.execute("select status, reject_reason from appointments where id = %s", (appt["id"],)).fetchone()
    assert row == {"status": "REJECTED", "reject_reason": "Clinic closed"}
    assert notifications(appt["id"])[-1] == "REJECTED"


def test_other_doctor_cannot_confirm(slots):
    appt = booking.create_appointment(req(slots[0]["id"]), now=NOON)
    with pytest.raises(booking.NotFound):
        booking.confirm(appt["id"], uuid4(), now=NOON)


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

def test_auto_cancel_at_1900_frees_slot_and_emails(slots, doctor):
    pending = booking.create_appointment(req(slots[0]["id"]), now=NOON)
    confirmed = booking.create_appointment(req(slots[1]["id"], phone="+919812345678"), now=NOON)
    booking.confirm(confirmed["id"], doctor["id"], now=NOON)

    assert booking.auto_cancel_pending(now=ist(TODAY, 18, 59)) == 0
    assert booking.auto_cancel_pending(now=ist(TODAY, 19, 0)) == 1

    assert status_of(pending["id"]) == "AUTO_CANCELLED"
    assert status_of(confirmed["id"]) == "CONFIRMED"
    assert notifications(pending["id"])[-1] == "AUTO_CANCELLED"
    # Slot is free again (would be bookable if booking were open)
    with db.transaction() as conn:
        n = conn.execute(
            "select count(*) as n from appointments where slot_id = %s and status in ('PENDING','CONFIRMED')",
            (slots[0]["id"],),
        ).fetchone()["n"]
    assert n == 0


def test_auto_cancel_catches_up_missed_days(slots):
    appt = booking.create_appointment(req(slots[0]["id"]), now=NOON)
    # Server was down at 19:00; restarted the next morning.
    assert booking.auto_cancel_pending(now=ist(TOMORROW, 8, 0)) == 1
    assert status_of(appt["id"]) == "AUTO_CANCELLED"


def test_confirm_vs_auto_cancel_exactly_one_outcome(slots, doctor):
    for i in range(5):
        with db.transaction() as conn:
            conn.execute("truncate notifications, calls, appointments cascade")
        appt = booking.create_appointment(req(slots[0]["id"]), now=NOON)
        barrier = threading.Barrier(2)
        outcome: dict[str, object] = {}

        def do_confirm():
            barrier.wait()
            try:
                booking.confirm(appt["id"], doctor["id"], now=ist(TODAY, 18, 59, 59))
                outcome["confirm"] = "ok"
            except (booking.Conflict, booking.Closed):
                outcome["confirm"] = "lost"

        def do_cancel():
            barrier.wait()
            outcome["cancelled"] = booking.auto_cancel_pending(now=ist(TODAY, 19, 0))

        threads = [threading.Thread(target=do_confirm), threading.Thread(target=do_cancel)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = status_of(appt["id"])
        if outcome["confirm"] == "ok":
            assert final == "CONFIRMED" and outcome["cancelled"] == 0, i
        else:
            assert final == "AUTO_CANCELLED" and outcome["cancelled"] == 1, i


def test_doctor_reminder_only_when_pending(slots):
    assert booking.send_doctor_reminder(now=ist(TODAY, 18, 30)) == 0
    assert notifications() == []
    booking.create_appointment(req(slots[0]["id"]), now=NOON)
    assert booking.send_doctor_reminder(now=ist(TODAY, 18, 30)) == 1
    assert notifications()[-1] == "DOCTOR_REMINDER"


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    # No `with` block: skip the lifespan so the test pool stays open.
    return TestClient(app)


def test_api_clinic(client, doctor):
    r = client.get("/api/clinic")
    assert r.json() == {"clinic_name": "Kumar Skin Clinic", "doctor_name": "Dr. Kumar", "specialty": "dermatology"}


def test_api_slots_and_booking(client, slots):
    with time_machine.travel(NOON, tick=False):
        r = client.get("/api/slots")
        assert r.status_code == 200
        assert len(r.json()["slots"]) == 4

        body = {
            "slot_id": str(slots[0]["id"]), "name": "  Priya   K ", "phone": "98765 43210",
            "email": "priya@test.in", "preferred_language": "ta", "consent_ai_call": True,
        }
        r = client.post("/api/appointments", json=body)
        assert r.status_code == 201, r.text

        r = client.post("/api/appointments", json=body)
        assert r.status_code == 409

    with db.transaction() as conn:
        p = conn.execute("select name, phone from patients").fetchone()
    assert p == {"name": "Priya K", "phone": "+919876543210"}


@pytest.mark.parametrize(
    "patch",
    [{"phone": "12345"}, {"phone": "+14155552671"}, {"email": "nope"}, {"preferred_language": "fr"}],
)
def test_api_booking_validation(client, slots, patch):
    body = {
        "slot_id": str(slots[0]["id"]), "name": "Priya", "phone": "9876543210",
        "email": "priya@test.in", "preferred_language": "ta", "consent_ai_call": True, **patch,
    }
    with time_machine.travel(NOON, tick=False):
        assert client.post("/api/appointments", json=body).status_code == 422


def test_api_booking_closed_after_1800(client, slots):
    body = {
        "slot_id": str(slots[0]["id"]), "name": "Priya", "phone": "9876543210",
        "email": "priya@test.in", "preferred_language": "ta", "consent_ai_call": True,
    }
    with time_machine.travel(ist(TODAY, 18, 0), tick=False):
        r = client.post("/api/appointments", json=body)
    assert r.status_code == 422
    assert "closed" in r.json()["detail"].lower()


def test_doctor_endpoints_require_auth(client):
    assert client.get("/api/doctor/appointments").status_code == 401


def test_doctor_api_confirm_flow(client, slots, doctor):
    app.dependency_overrides[current_doctor] = lambda: doctor
    try:
        with time_machine.travel(NOON, tick=False):
            appt = booking.create_appointment(req(slots[0]["id"]))
            r = client.get("/api/doctor/appointments", params={"date": str(TOMORROW), "status": "PENDING"})
            assert [a["id"] for a in r.json()] == [str(appt["id"])]

            r = client.post(f"/api/doctor/appointments/{appt['id']}/confirm")
            assert r.status_code == 200, r.text

            r = client.get(f"/api/doctor/appointments/{appt['id']}")
            detail = r.json()
            assert detail["status"] == "CONFIRMED"
            assert detail["calls"][0]["status"] == "QUEUED"

            r = client.post(f"/api/doctor/appointments/{appt['id']}/reject", json={"reason": "x"})
            assert r.status_code == 409
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Gaps found by mutation testing
# ---------------------------------------------------------------------------

def test_generate_tomorrows_slots_uses_ist_tomorrow(doctor):
    with time_machine.travel(NOON, tick=False):
        assert booking.generate_tomorrows_slots() == 4
    with db.transaction() as conn:
        rows = conn.execute("select date, start_time, end_time from slots order by start_time").fetchall()
    assert {r["date"] for r in rows} == {TOMORROW}
    assert [(str(r["start_time"]), str(r["end_time"])) for r in rows] == [
        ("10:00:00", "10:15:00"), ("10:15:00", "10:30:00"), ("10:30:00", "10:45:00"), ("10:45:00", "11:00:00"),
    ]


def test_booking_stores_consent(slots):
    appt = booking.create_appointment(req(slots[0]["id"]), now=NOON)
    with db.transaction() as conn:
        row = conn.execute("select consent_ai_call, consent_at from appointments where id = %s", (appt["id"],)).fetchone()
    assert row == {"consent_ai_call": True, "consent_at": NOON}


def test_block_and_unblock_slot(slots, doctor):
    blocked = booking.set_slot_blocked(slots[0]["id"], doctor["id"], True)
    assert blocked["id"] == slots[0]["id"] and blocked["blocked"] is True
    assert booking.set_slot_blocked(slots[0]["id"], doctor["id"], False)["blocked"] is False


def test_block_unknown_or_other_doctors_slot(slots, doctor):
    with pytest.raises(booking.NotFound):
        booking.set_slot_blocked(uuid4(), doctor["id"], True)
    with pytest.raises(booking.NotFound):
        booking.set_slot_blocked(slots[0]["id"], uuid4(), True)


def test_list_slots_shows_bookings(slots, doctor):
    appt = booking.create_appointment(req(slots[1]["id"]), now=NOON)
    rows = booking.list_slots(doctor["id"], TOMORROW)
    assert [r["id"] for r in rows] == [s["id"] for s in slots]
    assert rows[1]["appointment_id"] == appt["id"] and rows[1]["appointment_status"] == "PENDING"
    assert rows[0]["appointment_id"] is None


def body_for(slot_id, **over):
    b = {
        "slot_id": str(slot_id), "name": "Priya", "phone": "9876543210",
        "email": "priya@test.in", "preferred_language": "ta", "consent_ai_call": True,
    }
    b.update(over)
    return b


def test_api_clinic_404_without_doctor(client):
    r = client.get("/api/clinic")
    assert r.status_code == 404
    assert "seed" in r.json()["detail"]


def test_api_booking_unknown_slot_404(client, slots):
    with time_machine.travel(NOON, tick=False):
        assert client.post("/api/appointments", json=body_for(uuid4())).status_code == 404


def test_api_booking_without_consent_400(client, slots):
    with time_machine.travel(NOON, tick=False):
        r = client.post("/api/appointments", json=body_for(slots[0]["id"], consent_ai_call=False))
    assert r.status_code == 400
    assert "consent" in r.json()["detail"].lower()


@pytest.mark.parametrize(
    "name, ok",
    [("Al", True), ("A" * 100, True), ("A" * 101, False), (" A  ", False), ("A", False)],
)
def test_api_name_limits(client, slots, name, ok):
    with time_machine.travel(NOON, tick=False):
        r = client.post("/api/appointments", json=body_for(slots[0]["id"], name=name))
    assert r.status_code == (201 if ok else 422), r.text


@pytest.mark.parametrize("phone", ["abc", "", "+91 12345 67890", "044 2234 5678"])
def test_api_rejects_unparseable_phone(client, slots, phone):
    with time_machine.travel(NOON, tick=False):
        assert client.post("/api/appointments", json=body_for(slots[0]["id"], phone=phone)).status_code == 422


def test_doctor_api_responses(client, slots, doctor):
    app.dependency_overrides[current_doctor] = lambda: doctor
    try:
        with time_machine.travel(NOON, tick=False):
            a1 = booking.create_appointment(req(slots[0]["id"]))
            a2 = booking.create_appointment(req(slots[1]["id"], phone="+919812345678"))

            r = client.post(f"/api/doctor/appointments/{a1['id']}/confirm")
            assert r.json() == {"id": str(a1["id"]), "status": "CONFIRMED"}
            r = client.post(f"/api/doctor/appointments/{a2['id']}/reject", json={"reason": "Away"})
            assert r.json() == {"id": str(a2["id"]), "status": "REJECTED"}

            assert client.get(f"/api/doctor/appointments/{uuid4()}").status_code == 404
            assert client.post(f"/api/doctor/appointments/{uuid4()}/confirm").status_code == 404

            r = client.post(f"/api/doctor/slots/{slots[2]['id']}/block", json={"blocked": True})
            assert r.status_code == 200 and r.json()["blocked"] is True
            assert client.post(f"/api/doctor/slots/{uuid4()}/block").status_code == 404

            r = client.get("/api/doctor/slots")
            assert [s["blocked"] for s in r.json()] == [False, False, True, False]
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Family members sharing one phone number
# ---------------------------------------------------------------------------

def test_shared_phone_each_booking_keeps_its_own_name(slots, doctor):
    mohan = booking.create_appointment(req(slots[0]["id"], name="Mohan", email="mohan@test.in"), now=NOON)
    priya = booking.create_appointment(
        req(slots[1]["id"], name="Priya", email="priya2@test.in", preferred_language="hi"), now=NOON)

    rows = {r["id"]: r for r in booking.list_appointments(doctor["id"], TOMORROW)}
    assert rows[mohan["id"]]["patient_name"] == "Mohan"
    assert rows[priya["id"]]["patient_name"] == "Priya"
    assert rows[priya["id"]]["preferred_language"] == "hi"
    assert rows[mohan["id"]]["patient_phone"] == rows[priya["id"]]["patient_phone"] == "+919876543210"

    detail = booking.get_appointment_detail(mohan["id"], doctor["id"])
    assert (detail["patient_name"], detail["patient_email"], detail["preferred_language"]) == (
        "Mohan", "mohan@test.in", "ta")

    # one contact row for the phone
    with db.transaction() as conn:
        assert conn.execute("select count(*) as n from patients").fetchone()["n"] == 1
        recipients = [r["recipient"] for r in conn.execute(
            "select recipient from notifications where type = 'BOOKING_RECEIVED' order by sent_at")]
    assert recipients == ["mohan@test.in", "priya2@test.in"]


def test_shared_phone_confirm_email_goes_to_that_booking(slots, doctor):
    mohan = booking.create_appointment(req(slots[0]["id"], name="Mohan", email="mohan@test.in"), now=NOON)
    booking.create_appointment(req(slots[1]["id"], name="Priya", email="priya2@test.in"), now=NOON)
    booking.confirm(mohan["id"], doctor["id"], now=NOON)
    with db.transaction() as conn:
        n = conn.execute(
            "select recipient from notifications where type = 'CONFIRMED' and appointment_id = %s",
            (mohan["id"],)).fetchone()
    assert n["recipient"] == "mohan@test.in"


def test_pool_replaces_connection_closed_by_server(doctor):
    """Supabase's pooler drops idle connections; the pool must hand out a working one anyway."""
    pool = db.open_pool()
    with pool.connection() as conn:
        pid = conn.execute("select pg_backend_pid() as pid").fetchone()["pid"]
    with psycopg.connect(TEST_DB, autocommit=True) as admin:  # simulate the server closing it
        admin.execute("select pg_terminate_backend(%s)", (pid,))
    with db.transaction() as conn:
        assert conn.execute("select 1 as ok").fetchone()["ok"] == 1
