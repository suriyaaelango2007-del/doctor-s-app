"""Small in-memory rate limiter (spec §14: rate-limit the booking endpoint per IP and per phone).

The backend runs as a single process (the scheduler lives in it too), so an in-process
sliding window is enough. The per-phone rule lives in booking.create_appointment,
where it can be checked against the database.
"""

import math
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.config import get_settings


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> float | None:
        """Record a hit. Returns None if allowed, else seconds until the next hit is allowed."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return q[0] + self.window - now
            q.append(now)
            if len(self._hits) > 10_000:  # drop idle keys so memory stays bounded
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_ip(request: Request) -> str:
    """The caller's IP. Behind a reverse proxy set TRUST_PROXY_HEADERS=true to use X-Forwarded-For."""
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def limit(limiter: SlidingWindowLimiter, scope: str):
    """FastAPI dependency: 429 with Retry-After when the caller's IP is over the limit."""

    def dependency(request: Request) -> None:
        wait = limiter.hit(f"{scope}:{client_ip(request)}")
        if wait is not None:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait a few minutes and try again.",
                headers={"Retry-After": str(math.ceil(wait))},
            )

    return dependency


# Shared limiters (values from spec §14 hardening; tune in config if needed).
BOOKING_LIMITER = SlidingWindowLimiter(limit=10, window_seconds=3600)
FORM_LIMITER = SlidingWindowLimiter(limit=30, window_seconds=3600)
