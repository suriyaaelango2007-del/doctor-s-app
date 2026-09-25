import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db, time_rules
from app.config import get_settings
from app.jobs.scheduler import build_scheduler, run_startup_catchup
from app.routers import doctor, public
from app.services.booking import BookingError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("clinic")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db.open_pool()
    if settings.time_offset_minutes and settings.environment != "production":
        log.warning("TIME_OFFSET_MINUTES=%s — app clock is %s", settings.time_offset_minutes, time_rules.now_ist())

    scheduler = None
    if settings.scheduler_enabled:
        run_startup_catchup()
        scheduler = build_scheduler()
        scheduler.start()
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
        db.close_pool()


app = FastAPI(title="Clinic AI Intake", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(BookingError)
async def booking_error_handler(_: Request, exc: BookingError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


app.include_router(public.router)
app.include_router(doctor.router)


@app.get("/api/health")
def health():
    return {"ok": True, "now_ist": time_rules.now_ist()}
