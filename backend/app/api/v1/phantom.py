"""Ephemeral execution deliberately bypasses tasks, audit, retrieval and events."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import DbDep, Principal, get_current_principal
from app.core.config import get_settings
from app.models.user import ROLE_ADMIN, ROLE_OPERATOR
from app.schemas.phantom import PhantomRequest, PhantomResponse
from app.services.phantom import PhantomError, executor

router = APIRouter(prefix="/phantom", tags=["phantom"])
bearer = HTTPBearer(auto_error=False)


async def phantom_principal(
    db: DbDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)] = None,
) -> Principal:
    if credentials is None:
        raise HTTPException(401, "Phantom requires a user session")
    # Explicitly exclude API keys: resolving those updates last_used_at in SQL.
    principal = await get_current_principal(db, bearer=credentials, api_key=None)
    if principal.role not in {ROLE_ADMIN, ROLE_OPERATOR}:
        raise HTTPException(403, "Phantom access denied")
    return principal


PhantomDep = Annotated[Principal, Depends(phantom_principal)]


@router.get("/capabilities")
async def capabilities(_principal: PhantomDep) -> dict:
    settings = get_settings()
    return {
        "enabled": settings.phantom_enabled,
        "models": list(settings.phantom_models),
        "provider": "phantom_local",
        "cloud_allowed": False,
        "content_persistence": False,
        "forensic_zero_trace": False,
    }


@router.post("/tasks", response_model=PhantomResponse)
async def run(body: PhantomRequest, request: Request, _principal: PhantomDep) -> PhantomResponse:
    if not body.prompt.strip():
        raise HTTPException(422, "Phantom requires a nonblank prompt")
    work = asyncio.create_task(executor.execute(body))

    async def disconnected():
        # Body is already consumed; direct ASGI receive avoids a polling cancel
        # scope that could swallow cancellation of the monitor during cleanup.
        while True:
            message = await request.receive()
            if message["type"] == "http.disconnect":
                return

    monitor = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait({work, monitor}, return_when=asyncio.FIRST_COMPLETED)
        if work not in done:
            raise HTTPException(499, "Phantom caller disconnected")
        return await work
    except PhantomError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except Exception:
        raise HTTPException(502, "Phantom inference failed") from None
    finally:
        monitor.cancel()
        if not work.done():
            work.cancel()
        # Retrieving exceptions prevents asyncio from logging task payloads.
        await asyncio.gather(monitor, work, return_exceptions=True)
