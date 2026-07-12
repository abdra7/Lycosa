"""Multi-worker launch prerequisites (#4 phase 2, ADR-028).

Three per-process mechanisms must survive `uvicorn --workers N`:
- events published on one worker must reach WebSocket clients on every worker
  (Redis pub/sub event bus, opt-in via REDIS_URL);
- shared background jobs (offline sweeper, stuck-ingestion recovery) must run
  on exactly one worker (Redis leader gate);
- WORKERS>1 without REDIS_URL must refuse to start rather than silently run
  with N× limits and worker-local events.
Plus trusted-proxy X-Forwarded-For handling: IP-keyed throttles are wrong
behind a reverse proxy unless the forwarded client IP is used — but only when
the direct peer is a proxy we explicitly trust, or the header is spoofable.
"""

import asyncio

import fakeredis
import pytest
from httpx import AsyncClient
from starlette.requests import Request

from app.core.clientip import client_ip
from app.core.config import Settings, enforce_multiworker_prereqs, get_settings
from app.core.events import EventBus, RedisEventBus
from app.core.leader import LeaderGate
from app.core.ratelimit import reset_rate_limit

# ---------------------------------------------------------------------------
# Redis event bus: publish on worker A, receive on worker B
# ---------------------------------------------------------------------------


def _two_worker_buses() -> tuple[RedisEventBus, RedisEventBus]:
    server = fakeredis.FakeServer()
    return (
        RedisEventBus(fakeredis.FakeAsyncRedis(server=server)),
        RedisEventBus(fakeredis.FakeAsyncRedis(server=server)),
    )


async def test_redis_bus_delivers_across_workers() -> None:
    worker_a, worker_b = _two_worker_buses()
    queue_b = worker_b.subscribe()
    queue_a = worker_a.subscribe()  # the publisher's own clients also get it
    await asyncio.sleep(0.1)  # let the listener tasks attach to the channel
    try:
        worker_a.publish("node.connected", {"node_id": "n1"})

        event = await asyncio.wait_for(queue_b.get(), timeout=2)
        assert event["type"] == "node.connected"
        assert event["data"] == {"node_id": "n1"}
        assert "ts" in event

        own = await asyncio.wait_for(queue_a.get(), timeout=2)
        assert own["type"] == "node.connected"
    finally:
        await worker_a.aclose()
        await worker_b.aclose()


async def test_redis_bus_falls_back_to_local_delivery_when_redis_fails() -> None:
    """A Redis outage must not blind this worker's own dashboards."""
    worker_a, _ = _two_worker_buses()
    queue = worker_a.subscribe()
    await asyncio.sleep(0.05)

    async def boom(*args, **kwargs):
        raise ConnectionError("redis is down")

    worker_a._redis.publish = boom
    try:
        worker_a.publish("task.started", {"task_id": "t1"})
        event = await asyncio.wait_for(queue.get(), timeout=2)
        assert event["type"] == "task.started"
    finally:
        await worker_a.aclose()


async def test_in_process_bus_event_shape_unchanged() -> None:
    bus = EventBus()
    queue = bus.subscribe()
    bus.publish("alert.created", {"id": "a1"})
    event = queue.get_nowait()
    assert event["type"] == "alert.created"
    assert event["data"] == {"id": "a1"}
    assert "ts" in event


# ---------------------------------------------------------------------------
# leader gate: exactly one worker runs shared background jobs
# ---------------------------------------------------------------------------


def _two_worker_gates() -> tuple[LeaderGate, LeaderGate]:
    server = fakeredis.FakeServer()
    return (
        LeaderGate(fakeredis.FakeAsyncRedis(server=server), instance_id="worker-a"),
        LeaderGate(fakeredis.FakeAsyncRedis(server=server), instance_id="worker-b"),
    )


async def test_only_one_worker_leads() -> None:
    gate_a, gate_b = _two_worker_gates()
    assert await gate_a.try_lead("offline-sweeper", ttl_seconds=30) is True
    assert await gate_b.try_lead("offline-sweeper", ttl_seconds=30) is False
    # the holder renews, it doesn't fight itself
    assert await gate_a.try_lead("offline-sweeper", ttl_seconds=30) is True
    # different job names are independent leaderships
    assert await gate_b.try_lead("ingest-recovery", ttl_seconds=30) is True


async def test_leadership_fails_over_when_the_leader_dies() -> None:
    gate_a, gate_b = _two_worker_gates()
    assert await gate_a.try_lead("offline-sweeper", ttl_seconds=1) is True
    assert await gate_b.try_lead("offline-sweeper", ttl_seconds=1) is False
    await asyncio.sleep(1.1)  # gate A "crashed" and its lock expired
    assert await gate_b.try_lead("offline-sweeper", ttl_seconds=1) is True


async def test_no_redis_means_always_lead() -> None:
    # single-process default: the only worker is trivially the leader
    assert await LeaderGate(None).try_lead("offline-sweeper", ttl_seconds=30) is True


async def test_gate_stands_down_when_redis_errors() -> None:
    gate, _ = _two_worker_gates()

    async def boom(*args, **kwargs):
        raise ConnectionError("redis is down")

    gate._redis.set = boom
    assert await gate.try_lead("offline-sweeper", ttl_seconds=30) is False


# ---------------------------------------------------------------------------
# fail-fast: WORKERS>1 without REDIS_URL must not start
# ---------------------------------------------------------------------------


def test_multiworker_without_redis_refuses_to_start() -> None:
    settings = Settings(workers=4, redis_url="")
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        enforce_multiworker_prereqs(settings)


def test_multiworker_with_redis_is_allowed() -> None:
    enforce_multiworker_prereqs(Settings(workers=4, redis_url="redis://redis:6379/0"))


def test_single_worker_needs_no_redis() -> None:
    enforce_multiworker_prereqs(Settings(workers=1, redis_url=""))


# ---------------------------------------------------------------------------
# trusted-proxy client IP
# ---------------------------------------------------------------------------


def _request(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (peer, 1234)}
    )


@pytest.fixture
def _trusted_proxy():
    settings = get_settings()
    original = settings.trusted_proxies
    settings.trusted_proxies = "127.0.0.1, 10.0.0.0/8"
    yield
    settings.trusted_proxies = original


def test_xff_ignored_by_default() -> None:
    # no TRUSTED_PROXIES: the header is client-controlled, so it must not win
    assert get_settings().trusted_proxies == ""
    assert client_ip(_request("203.0.113.9", xff="1.2.3.4")) == "203.0.113.9"


def test_xff_from_untrusted_peer_is_ignored(_trusted_proxy) -> None:
    assert client_ip(_request("203.0.113.9", xff="1.2.3.4")) == "203.0.113.9"


def test_xff_from_trusted_peer_wins(_trusted_proxy) -> None:
    assert client_ip(_request("127.0.0.1", xff="203.0.113.9")) == "203.0.113.9"
    # CIDR entry: any 10.x proxy is trusted
    assert client_ip(_request("10.1.2.3", xff="203.0.113.9")) == "203.0.113.9"


def test_xff_takes_rightmost_untrusted_hop(_trusted_proxy) -> None:
    # attacker prepends junk; the trusted proxy appends the real client last —
    # the rightmost entry that is not our own proxy is the real client
    request = _request("127.0.0.1", xff="6.6.6.6, 203.0.113.9")
    assert client_ip(request) == "203.0.113.9"
    # proxy chain: entries belonging to trusted infra are skipped
    request = _request("127.0.0.1", xff="203.0.113.9, 10.0.0.7")
    assert client_ip(request) == "203.0.113.9"


def test_trusted_peer_with_no_xff_keeps_peer_ip(_trusted_proxy) -> None:
    assert client_ip(_request("127.0.0.1")) == "127.0.0.1"


async def test_rate_limit_buckets_key_on_forwarded_ip(client: AsyncClient, _trusted_proxy) -> None:
    # httpx ASGITransport presents peer 127.0.0.1, which the fixture trusts
    settings = get_settings()
    reset_rate_limit()
    settings.rate_limit_enabled = True
    settings.rate_limit_requests = 2
    settings.rate_limit_window_seconds = 60
    try:
        for _ in range(2):
            r = await client.get("/api/v1/me", headers={"X-Forwarded-For": "203.0.113.9"})
            assert r.status_code == 401
        limited = await client.get("/api/v1/me", headers={"X-Forwarded-For": "203.0.113.9"})
        assert limited.status_code == 429

        # a different forwarded client still has its own budget
        other = await client.get("/api/v1/me", headers={"X-Forwarded-For": "203.0.113.10"})
        assert other.status_code == 401
    finally:
        settings.rate_limit_enabled = False
        settings.rate_limit_requests = 120
        reset_rate_limit()
