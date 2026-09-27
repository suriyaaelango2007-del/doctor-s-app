import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db, time_rules
from app.config import get_settings
from app.jobs import scheduler
from app.routers import doctor, public, webhooks
from app.services import calls, summary
from app.services.booking import BookingError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("clinic")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db.open_pool()
    if settings.time_offset_minutes and settings.environment != "production":
        log.warning("TIME_OFFSET_MINUTES=%s — app clock is %s", settings.time_offset_minutes, time_rules.now_ist())

    if not calls.is_configured():
        log.warning("ElevenLabs is not configured - confirmed appointments will stay 'Call queued'")
    if not summary.is_configured():
        log.warning("LLM_API_KEY is not set - completed calls will not be summarised yet")
    if settings.scheduler_enabled:
        scheduler.run_startup_catchup()
        scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
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
app.include_router(webhooks.router)


@app.get("/api/health")
def health():
    return {"ok": True, "now_ist": time_rules.now_ist()}
