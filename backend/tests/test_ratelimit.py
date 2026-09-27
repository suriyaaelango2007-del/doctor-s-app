"""Spec §14: rate-limit booking (per IP and per phone) and the form endpoints."""

import pytest
import time_machine
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.ratelimit import BOOKING_LIMITER, FORM_LIMITER, SlidingWindowLimiter
from app.services import booking
from tests.conftest import TODAY, ist

NOON = ist(TODAY, 12)


def test_sliding_window():
    lim = SlidingWindowLimiter(limit=2, window_seconds=60)
    assert lim.hit("a", now=0) is None
    assert lim.hit("a", now=10) is None
    assert lim.hit("a", now=20) == 40  # oldest hit leaves the window at t=60
    assert lim.hit("b", now=20) is None  # other keys unaffected
    assert lim.hit("a", now=60) is None


def req(slot, phone="+919876543210", name="Mohan"):
    return booking.BookingRequest(slot_id=slot["id"], name=name, phone=phone, email="m@test.in",
                                  preferred_language="en", consent_ai_call=True)


def test_max_three_active_bookings_per_phone_per_day(slots, doctor):
    for i, name in enumerate(["Mohan", "Priya", "Ravi"]):
        booking.create_appointment(req(slots[i], name=name), now=NOON)
    with pytest.raises(booking.TooMany, match="already has 3 bookings"):
        booking.create_appointment(req(slots[3], name="Anu"), now=NOON)
    # a different phone is fine
    booking.create_appointment(req(slots[3], phone="+919812345678"), now=NOON)


def test_cancelled_bookings_do_not_count(slots, doctor):
    first = booking.create_appointment(req(slots[0]), now=NOON)
    booking.create_appointment(req(slots[1], name="Priya"), now=NOON)
    booking.create_appointment(req(slots[2], name="Ravi"), now=NOON)
    booking.reject(first["id"], doctor["id"], now=NOON)
    booking.create_appointment(req(slots[3], name="Anu"), now=NOON)


def body(slot, phone):
    return {"slot_id": str(slot["id"]), "name": "Mohan", "phone": phone, "email": "m@test.in",
            "preferred_language": "en", "consent_ai_call": True}


def test_api_booking_limited_per_ip(slots, monkeypatch):
    monkeypatch.setattr(BOOKING_LIMITER, "limit", 2)
    client = TestClient(app)
    with time_machine.travel(NOON, tick=False):
        assert client.post("/api/appointments", json=body(slots[0], "+919800000001")).status_code == 201
        assert client.post("/api/appointments", json=body(slots[0], "+919800000002")).status_code == 409
        r = client.post("/api/appointments", json=body(slots[1], "+919800000003"))
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    assert "Too many requests" in r.json()["detail"]


def test_api_per_phone_limit_is_429(slots):
    client = TestClient(app)
    with time_machine.travel(NOON, tick=False):
        for i in range(3):
            assert client.post("/api/appointments", json=body(slots[i], "+919876543210")).status_code == 201
        r = client.post("/api/appointments", json=body(slots[3], "+919876543210"))
    assert r.status_code == 429 and "already has 3 bookings" in r.json()["detail"]


def test_api_form_endpoints_limited(monkeypatch):
    monkeypatch.setattr(FORM_LIMITER, "limit", 3)
    client = TestClient(app)
    codes = [client.get("/api/forms/guess").status_code for _ in range(4)]
    assert codes == [404, 404, 404, 429]


def test_proxy_header_only_when_trusted(slots, monkeypatch):
    monkeypatch.setattr(BOOKING_LIMITER, "limit", 1)
    client = TestClient(app)
    with time_machine.travel(NOON, tick=False):
        h1, h2 = {"x-forwarded-for": "1.1.1.1"}, {"x-forwarded-for": "2.2.2.2"}
        # not trusted: both requests come from the same socket address -> second is limited
        assert client.post("/api/appointments", json=body(slots[0], "+919800000001"), headers=h1).status_code == 201
        assert client.post("/api/appointments", json=body(slots[1], "+919800000002"), headers=h2).status_code == 429
        BOOKING_LIMITER.reset()
        monkeypatch.setattr(get_settings(), "trust_proxy_headers", True)
        assert client.post("/api/appointments", json=body(slots[1], "+919800000003"), headers=h1).status_code == 201
        assert client.post("/api/appointments", json=body(slots[2], "+919800000004"), headers=h2).status_code == 201
        assert client.post("/api/appointments", json=body(slots[3], "+919800000005"), headers=h1).status_code == 429
