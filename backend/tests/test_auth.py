"""Doctor authentication: Supabase JWTs (ES256 via JWKS, legacy HS256 via secret)."""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app import auth
from app.config import get_settings
from app.main import app

HS_SECRET = "test-legacy-jwt-secret-that-is-long-enough"
SIGNING_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


class FakeJWKS:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=SIGNING_KEY.public_key())


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    monkeypatch.setattr(auth, "_jwks_client", lambda: FakeJWKS())


@pytest.fixture
def client():
    return TestClient(app)


def claims(sub, **over):
    c = {"sub": str(sub), "aud": "authenticated", "exp": int(time.time()) + 3600, "role": "authenticated"}
    c.update(over)
    return c


def es256(sub, key=SIGNING_KEY, **over):
    return jwt.encode(claims(sub, **over), key, algorithm="ES256", headers={"kid": "k1"})


def hs256(sub, **over):
    return jwt.encode(claims(sub, **over), HS_SECRET, algorithm="HS256")


def get_me(client, token):
    return client.get("/api/doctor/me", headers={"Authorization": f"Bearer {token}"})


def test_es256_token_for_linked_doctor(client, doctor):
    r = get_me(client, es256(doctor["auth_user_id"]))
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Dr. Kumar"


def test_valid_token_for_unlinked_user_is_forbidden(client, doctor):
    r = get_me(client, es256("00000000-0000-0000-0000-000000000001"))
    assert r.status_code == 403


def test_token_signed_by_wrong_key_rejected(client, doctor):
    assert get_me(client, es256(doctor["auth_user_id"], key=OTHER_KEY)).status_code == 401


def test_expired_token_rejected(client, doctor):
    assert get_me(client, es256(doctor["auth_user_id"], exp=int(time.time()) - 10)).status_code == 401


def test_wrong_audience_rejected(client, doctor):
    assert get_me(client, es256(doctor["auth_user_id"], aud="anon")).status_code == 401


def test_garbage_token_rejected(client):
    assert get_me(client, "not.a.jwt").status_code == 401


def test_missing_token_rejected(client):
    assert client.get("/api/doctor/me").status_code == 401


def test_hs256_accepted_when_secret_configured(client, doctor, monkeypatch):
    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", HS_SECRET)
    assert get_me(client, hs256(doctor["auth_user_id"])).status_code == 200


def test_hs256_rejected_without_secret(client, doctor, monkeypatch):
    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", "")
    assert get_me(client, hs256(doctor["auth_user_id"])).status_code == 401


def test_hs256_with_wrong_secret_rejected(client, doctor, monkeypatch):
    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", "a-different-secret-of-decent-length!!")
    assert get_me(client, hs256(doctor["auth_user_id"])).status_code == 401


def test_jwks_url_points_at_project(monkeypatch):
    monkeypatch.undo()  # use the real _jwks_client
    auth._jwks_client.cache_clear()
    try:
        assert auth._jwks_client().uri == "https://test-project.supabase.co/auth/v1/.well-known/jwks.json"
    finally:
        auth._jwks_client.cache_clear()
