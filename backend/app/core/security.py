"""Password hashing, JWT issuance/validation, and API key generation."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings

_password_hasher = PasswordHash.recommended()  # argon2id

API_KEY_HEADER = "X-API-Key"
_API_KEY_TAG = "lyc"


def hash_password(plain: str) -> str:
    return _password_hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _password_hasher.verify(plain, hashed)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_access_token(*, subject: str, role: str) -> tuple[str, str, datetime]:
    """Issue a JWT. Returns (token, jti, expires_at); jti is recorded server-side."""
    settings = get_settings()
    jti = uuid.uuid4().hex
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "jti": jti,
        "exp": expires_at,
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, jti, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT (signature + expiry). Raises jwt.PyJWTError on failure."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def generate_api_key() -> tuple[str, str, str]:
    """Generate an API key of the form ``lyc_<prefix>_<secret>``.

    Returns (full_key, prefix, key_hash). Only prefix (for lookup) and the
    SHA-256 of the full key are ever stored.
    """
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full_key = f"{_API_KEY_TAG}_{prefix}_{secret}"
    return full_key, prefix, sha256_hex(full_key)


def parse_api_key_prefix(full_key: str) -> str | None:
    """Extract the lookup prefix from a presented key; None if malformed."""
    parts = full_key.split("_", 2)
    if len(parts) != 3 or parts[0] != _API_KEY_TAG or not parts[1]:
        return None
    return parts[1]
