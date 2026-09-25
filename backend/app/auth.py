"""Doctor authentication: verify the Supabase access token sent by the frontend.

Newer Supabase projects sign tokens with asymmetric keys (ES256/RS256) published
at the JWKS endpoint; older ones use the shared HS256 JWT secret. We support both.
"""

from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.services import booking

bearer = HTTPBearer(auto_error=False)


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    url = f"{get_settings().supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def verify_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise jwt.InvalidTokenError("HS256 token but SUPABASE_JWT_SECRET is not set")
            key: Any = settings.supabase_jwt_secret
        else:
            key = _jwks_client().get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key,
            algorithms=["HS256", "RS256", "ES256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from exc


def current_doctor(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any]:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    claims = verify_token(creds.credentials)
    doctor = booking.get_doctor_by_auth_user(claims["sub"])
    if doctor is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is not linked to a doctor")
    return doctor


CurrentDoctor = Annotated[dict[str, Any], Depends(current_doctor)]
