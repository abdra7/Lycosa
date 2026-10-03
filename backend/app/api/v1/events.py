import asyncio
import logging
import uuid

import jwt as pyjwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.events import get_event_bus
from app.core.security import decode_access_token
from app.db.session import get_runtime_sessionmaker
from app.models import User
from app.services.auth import get_valid_session

logger = logging.getLogger("lycosa.events")

router = APIRouter()

# An idle stream re-checks its session at least this often, so a revoked or
# expired session is dropped even when no events are flowing (ADR-030).
REVALIDATE_IDLE_SECONDS = 30.0


def _token(websocket: WebSocket) -> str | None:
    """Bearer token via ?token= (WS-friendly) or Authorization header."""
    token = websocket.query_params.get("token")
    if token is None:
        auth = websocket.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.removeprefix("Bearer ")
    return token or None


async def _authorized(token: str) -> bool:
    """Same JWT + server-side session validation as REST. Re-run before every
    delivery, so logout, session expiry or token expiry ends the stream."""
    try:
        payload = decode_access_token(token)
    except pyjwt.PyJWTError:
        return False
    async with get_runtime_sessionmaker()() as db:
        session = await get_valid_session(db, payload["jti"])
        if session is None:
            return False
        user = (
            await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))
        ).scalar_one_or_none()
        return user is not None and user.is_active


@router.websocket("/events")
async def events_stream(websocket: WebSocket) -> None:
    """Live system events: node.*, task.*, workflow.*, alert.created."""
    token = _token(websocket)
    if token is None or not await _authorized(token):
        await websocket.close(code=4401, reason="unauthorized")
        return

    await websocket.accept()
    bus = get_event_bus()
    queue = bus.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), REVALIDATE_IDLE_SECONDS)
            except TimeoutError:
                event = None
            if not await _authorized(token):
                await websocket.close(code=4401, reason="session ended")
                return
            if event is not None:
                await websocket.send_json(event)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        bus.unsubscribe(queue)
