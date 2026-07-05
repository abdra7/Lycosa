"""Authentication service: credential checks, session lifecycle, API key resolution."""

import hmac
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    parse_api_key_prefix,
    sha256_hex,
    verify_password,
)
from app.models import ApiKey, Session, User


def as_utc(dt: datetime) -> datetime:
    """Normalize DB datetimes: SQLite returns naive, Postgres returns aware."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    """Return the user if credentials are valid and the account is active."""
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def create_user_session(db: AsyncSession, user: User) -> tuple[str, Session]:
    """Issue a JWT and record its JTI server-side so logout can revoke it."""
    token, jti, expires_at = create_access_token(subject=str(user.id), role=user.role.name)
    session = Session(user_id=user.id, token_hash=sha256_hex(jti), expires_at=expires_at)
    db.add(session)
    await db.flush()
    return token, session


async def get_valid_session(db: AsyncSession, jti: str) -> Session | None:
    """Look up a session by JTI; None if unknown, revoked, or expired."""
    token_hash = sha256_hex(jti)
    session = (
        await db.execute(select(Session).where(Session.token_hash == token_hash))
    ).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return None
    if as_utc(session.expires_at) < datetime.now(UTC):
        return None
    return session


async def revoke_session(db: AsyncSession, session: Session) -> None:
    session.revoked_at = datetime.now(UTC)
    await db.flush()


async def resolve_api_key(db: AsyncSession, presented_key: str) -> ApiKey | None:
    """Resolve a presented API key to its record; None if invalid/revoked/expired."""
    prefix = parse_api_key_prefix(presented_key)
    if prefix is None:
        return None
    api_key = (
        await db.execute(select(ApiKey).where(ApiKey.key_prefix == prefix))
    ).scalar_one_or_none()
    if api_key is None or api_key.revoked_at is not None:
        return None
    if api_key.expires_at is not None and as_utc(api_key.expires_at) < datetime.now(UTC):
        return None
    if not hmac.compare_digest(api_key.key_hash, sha256_hex(presented_key)):
        return None
    api_key.last_used_at = datetime.now(UTC)
    # commit, not flush: read-only endpoints never commit, and this runs at the
    # start of the request so nothing else is in the transaction yet
    await db.commit()
    return api_key
