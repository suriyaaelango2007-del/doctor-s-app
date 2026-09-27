"""Spec §14 hardening: headers, log redaction, production docs."""

import importlib
import logging

from fastapi.testclient import TestClient

from app.main import app
from app.security import RedactFormTokens, mask_email, redact_tokens


def test_security_headers_everywhere():
    r = TestClient(app).get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "cache-control" not in r.headers


def test_health_data_endpoints_not_cached():
    client = TestClient(app)
    assert client.get("/api/forms/x").headers["cache-control"] == "no-store"
    assert client.get("/api/doctor/appointments").headers["cache-control"] == "no-store"


def test_form_tokens_redacted_from_access_log():
    line = '%s - "%s %s HTTP/%s" %d'
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, line,
                            ("1.2.3.4", "POST", "/api/forms/AbC-123_xyz", "1.1", 204), None)
    RedactFormTokens().filter(rec)
    assert "AbC-123_xyz" not in rec.getMessage() and "/api/forms/<token>" in rec.getMessage()
    assert redact_tokens("GET /api/forms/secret?x=1") == "GET /api/forms/<token>?x=1"
    assert redact_tokens("GET /api/slots") == "GET /api/slots"


def test_mask_email():
    assert mask_email("priya.k@gmail.com") == "pr***@gmail.com"
    assert mask_email("bad") == "***"


def test_docs_hidden_in_production(monkeypatch):
    from app import config, main

    monkeypatch.setenv("ENVIRONMENT", "production")
    config.get_settings.cache_clear()
    try:
        prod = importlib.reload(main)
        client = TestClient(prod.app)
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    finally:
        monkeypatch.setenv("ENVIRONMENT", "test")
        config.get_settings.cache_clear()
        importlib.reload(main)


def test_docs_available_in_development():
    assert TestClient(app).get("/openapi.json").status_code == 200
