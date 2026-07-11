"""Per-IP brute-force throttle on /auth/login (ADR-023)."""

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.loginguard import reset_login_guard
from app.main import app

from .conftest import ADMIN_EMAIL, PASSWORD

# httpx ASGITransport reports 127.0.0.1 as request.client.host, so every
# request in a test shares one IP bucket — exactly what we want to exercise.


async def _login_client(sessionmaker_: async_sessionmaker[AsyncSession]) -> AsyncClient:
    from app.db.session import get_db, set_sessionmaker_override

    async def override_get_db():
        async with sessionmaker_() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    set_sessionmaker_override(sessionmaker_)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _post(client: AsyncClient, password: str, email: str = ADMIN_EMAIL):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_repeated_failures_get_throttled(users, sessionmaker_):
    settings = get_settings()
    reset_login_guard()
    settings.auth_max_failed_logins = 5
    settings.auth_login_window_seconds = 300
    client = await _login_client(sessionmaker_)
    try:
        for _ in range(5):
            r = await _post(client, "wrong-password")
            assert r.status_code == 401

        blocked = await _post(client, "wrong-password")
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "rate_limited"
        assert "Retry-After" in blocked.headers

        # even the CORRECT password is refused while the IP is locked
        assert (await _post(client, PASSWORD)).status_code == 429
    finally:
        await client.aclose()
        reset_login_guard()
        app.dependency_overrides.clear()


async def test_success_clears_failure_counter(users, sessionmaker_):
    settings = get_settings()
    reset_login_guard()
    settings.auth_max_failed_logins = 5
    settings.auth_login_window_seconds = 300
    client = await _login_client(sessionmaker_)
    try:
        for _ in range(4):  # one short of the limit
            assert (await _post(client, "wrong-password")).status_code == 401

        assert (await _post(client, PASSWORD)).status_code == 200  # resets the bucket

        # the counter is cleared, so four more failures still don't trip it
        for _ in range(4):
            assert (await _post(client, "wrong-password")).status_code == 401
    finally:
        await client.aclose()
        reset_login_guard()
        app.dependency_overrides.clear()


async def test_guard_disabled_when_max_is_zero(users, sessionmaker_):
    settings = get_settings()
    reset_login_guard()
    settings.auth_max_failed_logins = 0  # opt out
    client = await _login_client(sessionmaker_)
    try:
        for _ in range(10):
            assert (await _post(client, "wrong-password")).status_code == 401
    finally:
        await client.aclose()
        settings.auth_max_failed_logins = 5
        reset_login_guard()
        app.dependency_overrides.clear()
