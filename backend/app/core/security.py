"""Password hashing, JWT issuing/validation and webhook signature verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import AuthenticationError

TokenType = Literal["access", "refresh"]

_MAX_BCRYPT_BYTES = 72


# --------------------------------------------------------------------- password
def hash_password(password: str) -> str:
    """Hash a password with bcrypt.

    bcrypt silently truncates inputs beyond 72 bytes, so long passwords are
    pre-hashed with SHA-256 to preserve entropy.
    """
    payload = _prepare_password(password)
    return bcrypt.hashpw(payload, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare_password(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _prepare_password(password: str) -> bytes:
    raw = password.encode("utf-8")
    if len(raw) > _MAX_BCRYPT_BYTES:
        return hashlib.sha256(raw).hexdigest().encode("utf-8")
    return raw


# ------------------------------------------------------------------------- jwt
def create_token(
    subject: str,
    token_type: TokenType = "access",
    *,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    if expires_delta is None:
        expires_delta = (
            timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            if token_type == "access"
            else timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        )
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": uuid.uuid4().hex,
        "iss": settings.APP_NAME,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str, *, expected_type: TokenType | None = None) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.APP_NAME,
            options={"require": ["exp", "sub", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired", code="TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid authentication token", code="TOKEN_INVALID") from exc

    if expected_type and payload.get("type") != expected_type:
        raise AuthenticationError("Invalid token type", code="TOKEN_INVALID")
    return payload


def create_token_pair(subject: str, extra_claims: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "access_token": create_token(subject, "access", extra_claims=extra_claims),
        "refresh_token": create_token(subject, "refresh"),
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


# ------------------------------------------------------------------- webhooks
def compute_webhook_signature(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(payload: bytes, signature_header: str | None, secret: str) -> bool:
    """Constant-time verification of GitHub's ``X-Hub-Signature-256`` header."""
    if not signature_header or not secret:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = compute_webhook_signature(payload, secret)
    return hmac.compare_digest(expected, signature_header)


# --------------------------------------------------------------------- misc
def generate_state_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
