from datetime import date

import time_machine

from app import time_rules as tr
from tests.conftest import TODAY, TOMORROW, ist


def test_now_ist_follows_frozen_clock():
    with time_machine.travel(ist(TODAY, 17, 59), tick=False):
        assert tr.now_ist() == ist(TODAY, 17, 59)
        assert tr.tomorrow_ist() == TOMORROW


def test_time_offset_shifts_clock_outside_production(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "time_offset_minutes", -240)
    with time_machine.travel(ist(TODAY, 21, 0), tick=False):
        monkeypatch.setattr(s, "environment", "development")
        assert tr.now_ist() == ist(TODAY, 17, 0)
        monkeypatch.setattr(s, "environment", "production")
        assert tr.now_ist() == ist(TODAY, 21, 0)


def test_tomorrow_rolls_over_at_ist_midnight_not_utc():
    # 23:59 IST == 18:29 UTC; 00:01 IST next day == 18:31 UTC same UTC day
    assert tr.tomorrow_ist(ist(TODAY, 23, 59)) == TOMORROW
    assert tr.tomorrow_ist(ist(TOMORROW, 0, 1)) == date(2026, 9, 25)


def test_booking_open_boundary():
    assert tr.booking_open(ist(TODAY, 17, 59, 59))
    assert not tr.booking_open(ist(TODAY, 18, 0))
    assert not tr.booking_open(ist(TODAY, 23, 0))
    assert tr.booking_open(ist(TODAY, 0, 1))


def test_booking_open_uses_frozen_clock_by_default():
    with time_machine.travel(ist(TODAY, 17, 59), tick=False):
        assert tr.booking_open()
    with time_machine.travel(ist(TODAY, 18, 0), tick=False):
        assert not tr.booking_open()


def test_only_tomorrow_is_bookable():
    now = ist(TODAY, 12)
    assert tr.is_bookable_date(TOMORROW, now)
    assert not tr.is_bookable_date(TODAY, now)
    assert not tr.is_bookable_date(date(2026, 9, 25), now)


def test_decision_deadline_boundary():
    assert tr.can_decide(TOMORROW, ist(TODAY, 18, 59, 59))
    assert not tr.can_decide(TOMORROW, ist(TODAY, 19, 0))
    assert tr.decision_deadline(TOMORROW) == ist(TODAY, 19, 0)


def test_no_call_starts_after_1950():
    assert tr.can_start_call(TOMORROW, ist(TODAY, 19, 0))
    assert tr.can_start_call(TOMORROW, ist(TODAY, 19, 50))
    assert not tr.can_start_call(TOMORROW, ist(TODAY, 19, 50, 1))
    assert not tr.can_start_call(TOMORROW, ist(TODAY, 21, 0))
    # Not on the visit day itself
    assert not tr.can_start_call(TOMORROW, ist(TOMORROW, 8, 0))


def test_retry_rule():
    # confirmed at 18:58 -> first attempt fails -> retry at 19:13 is fine
    assert tr.retry_allowed(1, TOMORROW, ist(TODAY, 18, 58))
    # retry would start at 19:50 exactly -> allowed
    assert tr.retry_allowed(1, TOMORROW, ist(TODAY, 19, 35))
    # retry would start at 19:51 -> not allowed, send form instead
    assert not tr.retry_allowed(1, TOMORROW, ist(TODAY, 19, 36))
    # out of attempts
    assert not tr.retry_allowed(2, TOMORROW, ist(TODAY, 18, 0))


def test_form_expiry_is_clinic_opening():
    from datetime import time

    assert tr.form_expiry(TOMORROW, time(9, 0)) == ist(TOMORROW, 9, 0)
