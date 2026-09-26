"""In-process scheduled jobs (APScheduler). All cron times are IST.

Run a single backend process in production: multiple workers would each run
these jobs. (The jobs are idempotent, but duplicate emails would go out.)
"""

import logging
from collections.abc import Callable
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import config
from app.services import booking, calls

log = logging.getLogger("clinic.jobs")


_scheduler: BackgroundScheduler | None = None


def _safe(fn: Callable[[], object], quiet: bool = False) -> Callable[[], None]:
    """Wrap a job so one failure never kills the scheduler. `quiet` jobs log only when they did something."""

    def run() -> None:
        try:
            result = fn()
            if result or not quiet:
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
    sched.add_job(
        _safe(calls.process_call_queue, quiet=True),
        IntervalTrigger(seconds=config.CALL_WORKER_SECONDS),
        id="call_worker",
        max_instances=1,
    )
    sched.add_job(
        _safe(calls.recover_stuck_calls, quiet=True),
        IntervalTrigger(minutes=config.STUCK_CALL_CHECK_MINUTES),
        id="stuck_call_recovery",
        max_instances=1,
    )
    # Milestone 7: 20:00 form fallback for confirmed appointments without a completed call.
    return sched


def start() -> None:
    global _scheduler
    _scheduler = build_scheduler()
    _scheduler.start()


def shutdown() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def kick_call_worker() -> None:
    """Run the call worker now (e.g. right after a confirm) instead of waiting up to 30s."""
    if _scheduler and (job := _scheduler.get_job("call_worker")):
        job.modify(next_run_time=datetime.now(config.IST))


def run_startup_catchup() -> None:
    """Make state correct right after a (re)start: slots exist, overdue bookings cancelled."""
    _safe(booking.generate_tomorrows_slots)()
    _safe(booking.auto_cancel_pending)()
