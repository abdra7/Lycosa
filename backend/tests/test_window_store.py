"""Shared sliding-window state for rate limiting and the login guard (#4, ADR-027).

With multiple uvicorn workers, the in-process `_hits`/`_failures` dicts silently
multiply every limit by N (each worker keeps its own bucket). The window-store
abstraction keeps today's in-process behavior as the default and adds a
Redis-backed store (opt-in via REDIS_URL) whose state is shared across workers.
Cross-worker sharing is proven here with two store instances on one fakeredis
server — the same topology as two workers talking to one Redis.
"""

import asyncio

import fakeredis
import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.core.window_store import (
    InProcessWindowStore,
    RedisWindowStore,
    get_window_store,
    set_window_store,
)


def _two_worker_stores() -> tuple[RedisWindowStore, RedisWindowStore]:
    """Two store instances backed by ONE Redis — i.e. two uvicorn workers."""
    server = fakeredis.FakeServer()
    return (
        RedisWindowStore(fakeredis.FakeAsyncRedis(server=server)),
        RedisWindowStore(fakeredis.FakeAsyncRedis(server=server)),
    )


# ---------------------------------------------------------------------------
# rate-limit semantics (try_hit) across workers
# ---------------------------------------------------------------------------


async def test_redis_budget_is_shared_across_workers() -> None:
    worker_a, worker_b = _two_worker_stores()
    for _ in range(3):
        assert await worker_a.try_hit("rl:ip:10.0.0.9", limit=5, window_seconds=60) == 0
    for _ in range(2):
        assert await worker_b.try_hit("rl:ip:10.0.0.9", limit=5, window_seconds=60) == 0

    # 6th hit: worker B has only seen 2 locally, but the shared budget is spent
    retry_after = await worker_b.try_hit("rl:ip:10.0.0.9", limit=5, window_seconds=60)
    assert 1 <= retry_after <= 60
    assert await worker_a.try_hit("rl:ip:10.0.0.9", limit=5, window_seconds=60) >= 1


async def test_redis_rejected_hits_do_not_consume_budget() -> None:
    worker_a, _ = _two_worker_stores()
    for _ in range(2):
        assert await worker_a.try_hit("rl:ip:1", limit=2, window_seconds=60) == 0
    for _ in range(10):
        assert await worker_a.try_hit("rl:ip:1", limit=2, window_seconds=60) >= 1
    # rejections above must not have grown the bucket past the limit
    assert await worker_a._redis.zcard(worker_a._key("rl:ip:1")) == 2


async def test_redis_keys_are_independent() -> None:
    worker_a, worker_b = _two_worker_stores()
    assert await worker_a.try_hit("rl:ip:1", limit=1, window_seconds=60) == 0
    assert await worker_a.try_hit("rl:ip:1", limit=1, window_seconds=60) >= 1
    # another IP still has its own budget
    assert await worker_b.try_hit("rl:ip:2", limit=1, window_seconds=60) == 0


async def test_redis_window_expiry_frees_the_budget() -> None:
    worker_a, worker_b = _two_worker_stores()
    assert await worker_a.try_hit("rl:ip:1", limit=1, window_seconds=1) == 0
    assert await worker_b.try_hit("rl:ip:1", limit=1, window_seconds=1) >= 1
    await asyncio.sleep(1.1)
    assert await worker_b.try_hit("rl:ip:1", limit=1, window_seconds=1) == 0


# ---------------------------------------------------------------------------
# login-guard semantics (penalty / add / clear) across workers
# ---------------------------------------------------------------------------


async def test_redis_login_failures_shared_and_cleared_across_workers() -> None:
    worker_a, worker_b = _two_worker_stores()
    for _ in range(5):
        await worker_a.add("login:10.0.0.9", window_seconds=300)

    # worker B sees the lockout even though it recorded nothing
    assert await worker_b.penalty("login:10.0.0.9", limit=5, window_seconds=300) >= 1
    # under the limit is not locked
    assert await worker_b.penalty("login:10.0.0.9", limit=6, window_seconds=300) == 0

    # a successful login on worker B unlocks the IP everywhere
    await worker_b.clear("login:10.0.0.9")
    assert await worker_a.penalty("login:10.0.0.9", limit=5, window_seconds=300) == 0


async def test_redis_penalty_does_not_record_a_hit() -> None:
    worker_a, _ = _two_worker_stores()
    for _ in range(10):
        assert await worker_a.penalty("login:ip", limit=1, window_seconds=300) == 0
    await worker_a.add("login:ip", window_seconds=300)
    assert await worker_a.penalty("login:ip", limit=1, window_seconds=300) >= 1


# ---------------------------------------------------------------------------
# in-process store: same semantics, but per-process (the pre-#4 behavior)
# ---------------------------------------------------------------------------


async def test_inprocess_semantics_match() -> None:
    store = InProcessWindowStore()
    assert await store.try_hit("rl:k", limit=2, window_seconds=60) == 0
    assert await store.try_hit("rl:k", limit=2, window_seconds=60) == 0
    assert await store.try_hit("rl:k", limit=2, window_seconds=60) >= 1

    await store.add("login:k", window_seconds=300)
    assert await store.penalty("login:k", limit=1, window_seconds=300) >= 1
    await store.clear("login:k")
    assert await store.penalty("login:k", limit=1, window_seconds=300) == 0


async def test_inprocess_state_is_per_instance() -> None:
    # documents WHY multi-worker needs Redis: two workers, two budgets
    worker_a, worker_b = InProcessWindowStore(), InProcessWindowStore()
    assert await worker_a.try_hit("rl:k", limit=1, window_seconds=60) == 0
    assert await worker_a.try_hit("rl:k", limit=1, window_seconds=60) >= 1
    assert await worker_b.try_hit("rl:k", limit=1, window_seconds=60) == 0  # fresh bucket


def test_default_store_is_in_process_without_redis_url() -> None:
    set_window_store(None)  # drop any store a previous test installed
    assert get_settings().redis_url == ""
    assert isinstance(get_window_store(), InProcessWindowStore)


# ---------------------------------------------------------------------------
# wiring: the app's limiter and login guard run against a Redis-backed store
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_backed_store():
    store = RedisWindowStore(fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer()))
    set_window_store(store)
    yield store
    set_window_store(None)


async def test_rate_limit_middleware_works_on_redis_store(
    client: AsyncClient, redis_backed_store: RedisWindowStore
) -> None:
    settings = get_settings()
    settings.rate_limit_enabled = True
    settings.rate_limit_requests = 3
    settings.rate_limit_window_seconds = 60
    try:
        for _ in range(3):
            assert (await client.get("/api/v1/me")).status_code == 401
        limited = await client.get("/api/v1/me")
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate_limited"
        assert "Retry-After" in limited.headers
    finally:
        settings.rate_limit_enabled = False
        settings.rate_limit_requests = 120


async def test_rate_limit_fails_open_when_redis_is_down(
    client: AsyncClient, redis_backed_store: RedisWindowStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis outage must not take the whole API down with it (ADR-027): the
    limiter admits and logs rather than turning every request into a 5xx."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    async def boom(*args, **kwargs):
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(redis_backed_store, "try_hit", boom)
    settings = get_settings()
    settings.rate_limit_enabled = True
    settings.rate_limit_requests = 1
    try:
        for _ in range(3):
            assert (await client.get("/api/v1/me")).status_code == 401  # admitted, not 5xx/429
    finally:
        settings.rate_limit_enabled = False
        settings.rate_limit_requests = 120
