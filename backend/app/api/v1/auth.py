from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import DbDep, PrincipalDep
from app.core.clientip import client_ip
from app.core.config import get_settings
from app.core.loginguard import (
    clear_failures,
    is_locked_out,
    record_failure,
)
from app.models import Session
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.audit import audit
from app.services.auth import authenticate_user, create_user_session, revoke_session

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    # trusted-proxy aware (ADR-028): the login guard must throttle the real
    # client, not the reverse proxy's IP
    return client_ip(request)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: DbDep) -> TokenResponse:
    """Password login. Issues a bearer token; success and failure are both audited.

    A per-IP brute-force throttle (ADR-023) rejects further attempts once an IP
    accumulates too many recent failures; a successful login clears its counter.
    """
    settings = get_settings()
    ip = _client_ip(request)
    guard_on = settings.auth_max_failed_logins > 0 and ip is not None
    if guard_on:
        retry_after = await is_locked_out(
            ip,
            max_failures=settings.auth_max_failed_logins,
            window_seconds=settings.auth_login_window_seconds,
        )
        if retry_after:
            await audit(
                db,
                action="auth.login.throttled",
                detail={"email": body.email},
                ip_address=ip,
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts — try again later",
                headers={"Retry-After": str(retry_after)},
            )

    user = await authenticate_user(db, body.email, body.password)
    if user is None:
        if guard_on:
            await record_failure(ip, window_seconds=settings.auth_login_window_seconds)
        await audit(
            db,
            action="auth.login.failure",
            detail={"email": body.email},
            ip_address=ip,
        )
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if guard_on:
        await clear_failures(ip)
    token, session = await create_user_session(db, user)
    await audit(
        db,
        action="auth.login.success",
        actor_user_id=user.id,
        resource_type="session",
        resource_id=str(session.id),
        ip_address=_client_ip(request),
    )
    await db.commit()
    return TokenResponse(
        access_token=token,
        expires_in=get_settings().access_token_expire_minutes * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(principal: PrincipalDep, request: Request, db: DbDep) -> None:
    """Revoke the current session server-side. API-key callers have no session."""
    if principal.type != "user" or principal.session_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Logout applies to user sessions only",
        )
    session = (
        await db.execute(select(Session).where(Session.id == principal.session_id))
    ).scalar_one()
    await revoke_session(db, session)
    await audit(
        db,
        action="auth.logout",
        actor_user_id=principal.id,
        resource_type="session",
        resource_id=str(session.id),
        ip_address=_client_ip(request),
    )
    await db.commit()
