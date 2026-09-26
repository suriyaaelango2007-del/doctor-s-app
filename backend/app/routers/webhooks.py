"""ElevenLabs post-call webhooks (spec §8): POST /api/webhooks/elevenlabs."""

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app import config
from app.config import get_settings
from app.services import calls

log = logging.getLogger("clinic.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def verify_signature(raw_body: bytes, header: str | None, secret: str, now: float | None = None) -> None:
    """Check `elevenlabs-signature: t=<unix>,v0=<hex hmac-sha256 of "t.body">`. Raises 401."""
    if not header:
        raise HTTPException(401, "Missing signature")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    timestamp, signature = parts.get("t"), parts.get("v0")
    if not timestamp or not signature or not timestamp.isdigit():
        raise HTTPException(401, "Malformed signature")
    now = time.time() if now is None else now
    if int(timestamp) < now - config.WEBHOOK_TOLERANCE_SECONDS:
        raise HTTPException(401, "Signature timestamp too old")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Invalid signature")


@router.post("/elevenlabs")
async def elevenlabs_webhook(request: Request):
    secret = get_settings().elevenlabs_webhook_secret
    if not secret:
        raise HTTPException(503, "ELEVENLABS_WEBHOOK_SECRET is not configured")
    raw = await request.body()
    verify_signature(raw, request.headers.get("elevenlabs-signature"), secret)
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid JSON") from exc

    # DB work is blocking; run it off the event loop.
    outcome = await run_in_threadpool(calls.handle_webhook, event)
    conversation_id = (event.get("data") or {}).get("conversation_id")
    if outcome == "unknown_call":
        log.warning("Webhook %s for unknown conversation %s", event.get("type"), conversation_id)
    else:
        log.info("Webhook %s for %s: %s", event.get("type"), conversation_id, outcome)
    # Always 200 once verified, so ElevenLabs doesn't retry things we chose to ignore.
    return {"ok": True, "outcome": outcome}
