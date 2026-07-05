"""Shared API dependencies: DB session, current principal, role guards."""

import uuid
from typing import Annotated

import jwt as pyjwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import API_KEY_HEADER, decode_access_token
from app.db.session import get_db
from app.models import User
from app.services.auth import get_valid_session, resolve_api_key

_bearer_scheme = HTTPBearer(auto_error=False)
_api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

DbDep = Annotated[AsyncSession, Depends(get_db)]


class Principal(BaseModel):
    """The authenticated caller: a user (via JWT) or a service/node (via API key)."""

    type: str  # "user" | "api_key"
    id: uuid.UUID
    role: str
    email: str | None = None
    name: str | None = None
    session_id: uuid.UUID | None = None  # set for user principals; enables logout


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_principal(
    db: DbDep,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)] = None,
    api_key: Annotated[str | None, Security(_api_key_scheme)] = None,
) -> Principal:
    if bearer is not None:
        try:
            payload = decode_access_token(bearer.credentials)
        except pyjwt.PyJWTError:
            raise _unauthorized("Invalid or expired token") from None
        session = await get_valid_session(db, payload["jti"])
        if session is None:
            raise _unauthorized("Session revoked or expired")
        user = (
            await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            raise _unauthorized("User not found or inactive")
        return Principal(
            type="user",
            id=user.id,
            role=user.role.name,
            email=user.email,
            session_id=session.id,
        )

    if api_key is not None:
        record = await resolve_api_key(db, api_key)
        if record is None:
            raise _unauthorized("Invalid API key")
        return Principal(type="api_key", id=record.id, role=record.role.name, name=record.name)

    raise _unauthorized("Not authenticated")


PrincipalDep = Annotated[Principal, Depends(get_current_principal)]


def require_roles(*roles: str):
    """Route guard: 403 unless the principal's role is one of `roles`."""

    async def _check(principal: PrincipalDep) -> Principal:
        if principal.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(roles)}",
            )
        return principal

    return _check
