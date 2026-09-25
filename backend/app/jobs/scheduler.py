"""In-process scheduled jobs (APScheduler). All cron times are IST.

Run a single backend process in production: multiple workers would each run
these jobs. (The jobs are idempotent, but duplicate emails would go out.)
"""

import logging
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import config
from app.services import booking

log = logging.getLogger("clinic.jobs")


def _safe(fn: Callable[[], object]) -> Callable[[], None]:
    def run() -> None:
        try:
            result = fn()
            log.info("Job %s finished: %s", fn.__name__, result)
        except Exception:  # noqa: BLE001
            log.exception("Job %s failed", fn.__name__)

    run.__name__ = run.__qualname__ = fn.__name__
    return run


def _cron(t) -> CronTrigger:
    return CronTrigger(hour=t.hour, minute=t.minute, timezone=config.IST)


def build_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=config.IST, job_defaults={"coalesce": True, "misfire_grace_time": 600})
    sched.add_job(_safe(booking.generate_tomorrows_slots), _cron(config.SLOT_GENERATION), id="slot_generation")
    sched.add_job(_safe(booking.send_doctor_reminder), _cron(config.DOCTOR_REMINDER), id="doctor_reminder")
    sched.add_job(_safe(booking.auto_cancel_pending), _cron(config.DECISION_DEADLINE), id="auto_cancel")
    # Milestone 5+: call queue worker (every 30s), retry/form fallback (20:00),
    # missed-webhook recovery (every 5 min).
    return sched


def run_startup_catchup() -> None:
    """Make state correct right after a (re)start: slots exist, overdue bookings cancelled."""
    _safe(booking.generate_tomorrows_slots)()
    _safe(booking.auto_cancel_pending)()
