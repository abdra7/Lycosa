"""API-gateway concerns: error envelope shape and rate limiting."""

from httpx import AsyncClient

from app.core.config import get_settings
from app.core.ratelimit import reset_rate_limit
from app.core.security import API_KEY_HEADER


async def test_envelope_on_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/me")
    body = response.json()
    assert response.status_code == 401
    assert body["error"]["code"] == "unauthorized"
    assert isinstance(body["error"]["message"], str)


async def test_envelope_on_404_route(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_envelope_on_validation_error(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": "x"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any("password" in d["field"] for d in error["details"])


async def test_rate_limit_returns_429_envelope(client: AsyncClient) -> None:
    settings = get_settings()
    reset_rate_limit()  # start from an empty bucket (limiter state is process-wide)
    settings.rate_limit_enabled = True
    settings.rate_limit_requests = 3
    settings.rate_limit_window_seconds = 60
    try:
        for _ in range(3):
            response = await client.get("/api/v1/me")
            assert response.status_code == 401  # authenticated? no — but not rate-limited

        limited = await client.get("/api/v1/me")
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate_limited"
        assert "Retry-After" in limited.headers
    finally:
        settings.rate_limit_enabled = False
        settings.rate_limit_requests = 120
        reset_rate_limit()


async def test_rate_limit_not_bypassed_by_rotating_api_key(client: AsyncClient) -> None:
    """F-2 (ADR-020): a caller must not escape the limit by sending a different
    X-API-Key header on each request. Keying is by client IP, so rotating the
    header still lands in the same bucket."""
    settings = get_settings()
    reset_rate_limit()
    settings.rate_limit_enabled = True
    settings.rate_limit_requests = 3
    settings.rate_limit_window_seconds = 60
    try:
        for i in range(3):
            r = await client.get("/api/v1/me", headers={API_KEY_HEADER: f"bogus-key-{i}"})
            assert r.status_code == 401  # not throttled yet, despite a new key each time

        # a fourth request with yet another fresh key is still rate-limited
        limited = await client.get("/api/v1/me", headers={API_KEY_HEADER: "bogus-key-99"})
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate_limited"
    finally:
        settings.rate_limit_enabled = False
        settings.rate_limit_requests = 120
        reset_rate_limit()


async def test_healthz_exempt_from_rate_limit(client: AsyncClient) -> None:
    settings = get_settings()
    settings.rate_limit_enabled = True
    settings.rate_limit_requests = 1
    try:
        for _ in range(5):
            assert (await client.get("/healthz")).status_code == 200
    finally:
        settings.rate_limit_enabled = False
        settings.rate_limit_requests = 120
