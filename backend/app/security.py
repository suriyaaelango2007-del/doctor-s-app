"""Security hardening (spec §14): response headers, log redaction."""

import logging
import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Form tokens are bearer secrets: keep them out of access logs.
_FORM_TOKEN = re.compile(r"(/api/forms/)[^/\s?\"]+")

# Health data must not be cached by browsers or proxies.
_NO_STORE_PREFIXES = ("/api/doctor", "/api/forms")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


def mask_email(address: str) -> str:
    """priya.k@gmail.com -> pr***@gmail.com (for logs)."""
    local, _, domain = (address or "").partition("@")
    return f"{local[:2]}***@{domain}" if domain else "***"


def redact_tokens(text: str) -> str:
    return _FORM_TOKEN.sub(r"\1<token>", text)


class RedactFormTokens(logging.Filter):
    """Uvicorn access-log filter: /api/forms/<secret> -> /api/forms/<token>."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args and isinstance(record.args, tuple):
            record.args = tuple(redact_tokens(a) if isinstance(a, str) else a for a in record.args)
        record.msg = redact_tokens(str(record.msg))
        return True


def install_log_redaction() -> None:
    logging.getLogger("uvicorn.access").addFilter(RedactFormTokens())


class SecurityHeadersMiddleware:
    """Adds security headers to every response and no-store to health-data endpoints."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        no_store = scope["path"].startswith(_NO_STORE_PREFIXES)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                headers += [(k, v) for k, v in SECURITY_HEADERS if k not in existing]
                if no_store:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
