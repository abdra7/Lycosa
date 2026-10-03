"""Security audit 2026-10 (ADR-030): login-guard atomicity, multi-line
X-Forwarded-For, and event-stream session re-validation."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

import app.api.v1.auth as auth_api
from app.core.clientip import client_ip
from app.core.config import get_settings
from app.core.loginguard import reset_login_guard
from app.core.security import API_KEY_HEADER
from app.main import app
from app.models import Session
from tests.conftest import ADMIN_EMAIL, PASSWORD, bearer, login
from tests.test_login_guard import _login_client, _post
from tests.test_nodes_register import payload
from tests.test_observability import METRICS


@pytest.fixture
def _guard_of_three():
    settings = get_settings()
    original = (settings.auth_max_failed_logins, settings.auth_login_window_seconds)
    reset_login_guard()
    settings.auth_max_failed_logins = 3
    settings.auth_login_window_seconds = 300
    yield
    settings.auth_max_failed_logins, settings.auth_login_window_seconds = original
    reset_login_guard()
    app.dependency_overrides.clear()


@pytest.fixture
def _slow_auth(monkeypatch):
    """Widen the check-to-record window the way a slow DB/Argon2 would."""
    real = auth_api.authenticate_user
    calls = [0]

    async def slow(db, email, password):
        calls[0] += 1
        await asyncio.sleep(0.2)
        return await real(db, email, password)

    monkeypatch.setattr(auth_api, "authenticate_user", slow)
    return calls


async def test_concurrent_failures_respect_cap(users, sessionmaker_, _guard_of_three, _slow_auth):
    client = await _login_client(sessionmaker_)
    try:
        responses = await asyncio.gather(*[_post(client, "wrong-password") for _ in range(8)])
    finally:
        await client.aclose()

    codes = [r.status_code for r in responses]
    assert _slow_auth[0] <= 3, codes
    assert codes.count(401) <= 3 and set(codes) <= {401, 429}


async def test_correct_password_in_burst_is_still_throttled(
    users, sessionmaker_, _guard_of_three, _slow_auth
):
    client = await _login_client(sessionmaker_)
    try:
        attempts = [_post(client, "wrong-password") for _ in range(7)] + [_post(client, PASSWORD)]
        responses = await asyncio.gather(*attempts)
    finally:
        await client.aclose()

    assert _slow_auth[0] <= 3
    assert sum(r.status_code != 429 for r in responses) <= 3


@pytest.fixture
def _trusted_loopback():
    settings = get_settings()
    original = settings.trusted_proxies
    settings.trusted_proxies = "127.0.0.1"
    yield
    settings.trusted_proxies = original


def test_xff_joins_every_field_line(_trusted_loopback) -> None:
    """A proxy that appends its own X-Forwarded-For line (instead of merging)
    must still win over the line the client sent."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"6.6.6.6"), (b"x-forwarded-for", b"203.0.113.9")],
            "client": ("127.0.0.1", 1234),
        }
    )
    assert client_ip(request) == "203.0.113.9"


async def _register_and_connect(client: AsyncClient, node_api_key: tuple) -> tuple[str, str]:
    token = await login(client, ADMIN_EMAIL)
    full_key, _ = node_api_key
    await client.post("/api/v1/nodes/register", json=payload(), headers={API_KEY_HEADER: full_key})
    return token, full_key


async def test_event_stream_closes_after_logout(
    client: AsyncClient, users: dict, node_api_key: tuple
) -> None:
    token, full_key = await _register_and_connect(client, node_api_key)
    with TestClient(app) as tc, tc.websocket_connect(f"/api/v1/events?token={token}") as ws:
        assert (await client.post("/api/v1/auth/logout", headers=bearer(token))).status_code == 204
        await client.post(
            "/api/v1/nodes/heartbeat", json={"metrics": METRICS}, headers={API_KEY_HEADER: full_key}
        )
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401


async def test_event_stream_closes_after_session_expiry(
    client: AsyncClient, users: dict, node_api_key: tuple, db_session: AsyncSession
) -> None:
    token, full_key = await _register_and_connect(client, node_api_key)
    with TestClient(app) as tc, tc.websocket_connect(f"/api/v1/events?token={token}") as ws:
        session = (
            await db_session.execute(select(Session).where(Session.user_id == users["admin"].id))
        ).scalar_one()
        session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db_session.commit()
        await client.post(
            "/api/v1/nodes/heartbeat", json={"metrics": METRICS}, headers={API_KEY_HEADER: full_key}
        )
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401
