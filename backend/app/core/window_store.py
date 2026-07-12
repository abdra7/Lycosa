"""Sliding-window hit store behind the rate limiter and login guard (ADR-027).

Both throttles count timestamped hits per key inside a rolling window. Their
buckets used to live in per-process dicts, which silently multiplies every
limit by N under `uvicorn --workers N`. This module keeps that in-process
behavior as the default and adds a Redis-backed store (opt-in via REDIS_URL)
whose state is shared by every worker.

Semantics, identical in both backends:
- ``try_hit``: admit-and-record unless the key already has `limit` hits in the
  window; a rejected hit consumes no budget. Returns 0 when admitted, else
  seconds until the oldest hit leaves the window (the Retry-After value).
- ``penalty``: like the rejection check of ``try_hit`` but never records.
- ``add`` / ``clear``: record a hit / drop the key (login guard: a failure /
  a successful login).
"""

import time
from abc import ABC, abstractmethod
from collections import deque
from uuid import uuid4

from app.core.config import get_settings


class WindowStore(ABC):
    @abstractmethod
    async def try_hit(self, key: str, *, limit: int, window_seconds: int) -> int:
        """Record a hit and return 0, or (over `limit`) record nothing and
        return seconds until the oldest hit ages out."""

    @abstractmethod
    async def penalty(self, key: str, *, limit: int, window_seconds: int) -> int:
        """Seconds until `key` drops below `limit` hits, or 0 if it is below.
        Never records a hit."""

    @abstractmethod
    async def add(self, key: str, *, window_seconds: int) -> None:
        """Record a hit unconditionally."""

    @abstractmethod
    async def clear(self, key: str) -> None:
        """Forget every hit for `key`."""


def _retry_after(oldest: float, window_start: float) -> int:
    return max(1, int(oldest - window_start) + 1)


class InProcessWindowStore(WindowStore):
    """Per-process dict-of-deques — the pre-ADR-027 behavior, still the default.
    Uses the monotonic clock; state is invisible to other workers."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    @staticmethod
    def _prune(bucket: deque[float], window_start: float) -> None:
        while bucket and bucket[0] < window_start:
            bucket.popleft()

    async def try_hit(self, key: str, *, limit: int, window_seconds: int) -> int:
        now = time.monotonic()
        window_start = now - window_seconds
        bucket = self._hits.setdefault(key, deque())
        self._prune(bucket, window_start)
        if len(bucket) >= limit:
            return _retry_after(bucket[0], window_start)
        bucket.append(now)
        return 0

    async def penalty(self, key: str, *, limit: int, window_seconds: int) -> int:
        bucket = self._hits.get(key)
        if not bucket:
            return 0
        window_start = time.monotonic() - window_seconds
        self._prune(bucket, window_start)
        if len(bucket) < limit:
            return 0
        return _retry_after(bucket[0], window_start)

    async def add(self, key: str, *, window_seconds: int) -> None:
        now = time.monotonic()
        bucket = self._hits.setdefault(key, deque())
        self._prune(bucket, now - window_seconds)
        bucket.append(now)

    async def clear(self, key: str) -> None:
        self._hits.pop(key, None)

    def clear_prefix(self, prefix: str) -> None:
        """Drop every bucket under `prefix` (test isolation)."""
        for key in [k for k in self._hits if k.startswith(prefix)]:
            del self._hits[key]


class RedisWindowStore(WindowStore):
    """One sorted set per key: member = unique id, score = wall-clock time
    (`time.time()` — the only clock that agrees across processes). Every op
    prunes entries older than the window; keys expire shortly after their
    window so idle IPs cost nothing."""

    def __init__(self, client) -> None:  # redis.asyncio.Redis (or fakeredis)
        self._redis = client

    @staticmethod
    def _key(key: str) -> str:
        return f"lycosa:window:{key}"

    async def try_hit(self, key: str, *, limit: int, window_seconds: int) -> int:
        now = time.time()
        window_start = now - window_seconds
        name = self._key(key)
        member = f"{now:.6f}:{uuid4().hex}"
        # add first, then check: two workers racing a plain check-then-add
        # would both admit the last budget slot
        pipe = self._redis.pipeline(transaction=True)
        pipe.zremrangebyscore(name, 0, window_start)
        pipe.zadd(name, {member: now})
        pipe.zcard(name)
        pipe.zrange(name, 0, 0, withscores=True)
        pipe.expire(name, window_seconds + 60)
        _, _, count, oldest, _ = await pipe.execute()
        if count <= limit:
            return 0
        await self._redis.zrem(name, member)  # rejected hits consume no budget
        return _retry_after(oldest[0][1], window_start)

    async def penalty(self, key: str, *, limit: int, window_seconds: int) -> int:
        now = time.time()
        window_start = now - window_seconds
        name = self._key(key)
        pipe = self._redis.pipeline(transaction=True)
        pipe.zremrangebyscore(name, 0, window_start)
        pipe.zcard(name)
        pipe.zrange(name, 0, 0, withscores=True)
        _, count, oldest = await pipe.execute()
        if count < limit or not oldest:
            return 0
        return _retry_after(oldest[0][1], window_start)

    async def add(self, key: str, *, window_seconds: int) -> None:
        now = time.time()
        name = self._key(key)
        pipe = self._redis.pipeline(transaction=True)
        pipe.zadd(name, {f"{now:.6f}:{uuid4().hex}": now})
        pipe.expire(name, window_seconds + 60)
        await pipe.execute()

    async def clear(self, key: str) -> None:
        await self._redis.delete(self._key(key))


_store: WindowStore | None = None


def get_window_store() -> WindowStore:
    """Process-wide store: Redis-backed when REDIS_URL is set, else in-process."""
    global _store
    if _store is None:
        url = get_settings().redis_url
        if url:
            import redis.asyncio as aioredis

            _store = RedisWindowStore(aioredis.from_url(url))
        else:
            _store = InProcessWindowStore()
    return _store


def set_window_store(store: WindowStore | None) -> None:
    """Install a specific store (tests); None re-derives from settings."""
    global _store
    _store = store
