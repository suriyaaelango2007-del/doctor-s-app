"""Every IST deadline check lives here. Nothing else reads the clock."""

from datetime import date, datetime, time, timedelta

from app import config
from app.config import IST, get_settings


def now_ist() -> datetime:
    now = datetime.now(IST)
    settings = get_settings()
    if settings.environment != "production" and settings.time_offset_minutes:
        now += timedelta(minutes=settings.time_offset_minutes)
    return now


def today_ist(now: datetime | None = None) -> date:
    return (now or now_ist()).date()


def tomorrow_ist(now: datetime | None = None) -> date:
    return today_ist(now) + timedelta(days=1)


def at(d: date, t: time) -> datetime:
    """Combine a date and a wall-clock time into an aware IST datetime."""
    return datetime.combine(d, t, tzinfo=IST)


def booking_open(now: datetime | None = None) -> bool:
    now = now or now_ist()
    return now.timetz().replace(tzinfo=None) < config.BOOKING_CUTOFF


def is_bookable_date(slot_date: date, now: datetime | None = None) -> bool:
    """Patients can book slots for tomorrow only."""
    return slot_date == tomorrow_ist(now)


def decision_deadline(visit_date: date) -> datetime:
    """19:00 IST on the day before the visit."""
    return at(visit_date - timedelta(days=1), config.DECISION_DEADLINE)


def can_decide(visit_date: date, now: datetime | None = None) -> bool:
    """Doctor may confirm/reject until 19:00 the day before the visit."""
    now = now or now_ist()
    return now < decision_deadline(visit_date)


def last_call_start(visit_date: date) -> datetime:
    return at(visit_date - timedelta(days=1), config.LAST_CALL_START)


def can_start_call(visit_date: date, now: datetime | None = None) -> bool:
    """No call attempt may start after 19:50 the day before the visit."""
    now = now or now_ist()
    day_before = visit_date - timedelta(days=1)
    return now.date() == day_before and now <= last_call_start(visit_date)


def retry_allowed(attempt: int, visit_date: date, now: datetime | None = None) -> bool:
    """Retry only if attempts remain and the retry would still start by 19:50."""
    now = now or now_ist()
    if attempt >= config.MAX_CALL_ATTEMPTS:
        return False
    return now + timedelta(minutes=config.RETRY_DELAY_MIN) <= last_call_start(visit_date)


def form_expiry(visit_date: date, clinic_open_time: time) -> datetime:
    """Fallback form links expire at clinic opening time on the visit day."""
    return at(visit_date, clinic_open_time)
