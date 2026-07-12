"""Single-leader gate for shared background jobs (ADR-028).

The offline sweeper and stuck-ingestion recovery used to run in the (single)
process by definition. Under `uvicorn --workers N` every worker would run
them: redundant DB writes and a startup race on recovery. `try_lead` elects
exactly one worker per job via a Redis lock (`SET NX EX` + renewal by the
holder); with no Redis configured there is only one worker, which trivially
leads. If Redis errors, the gate stands down (returns False) and logs — the
job pauses until Redis returns rather than running N times.
"""

import logging
from uuid import uuid4

from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger("lycosa.leader")


class LeaderGate:
    def __init__(self, client, instance_id: str | None = None) -> None:
        self._redis = client  # None = single-process: always lead
        self._id = instance_id or uuid4().hex

    async def try_lead(self, name: str, *, ttl_seconds: int) -> bool:
        """True if this process holds (or just took/renewed) the `name` lock.
        The TTL must comfortably exceed the job interval so the leader keeps
        renewing; a dead leader's lock expires and another worker takes over."""
        if self._redis is None:
            return True
        key = f"lycosa:leader:{name}"
        try:
            if await self._redis.set(key, self._id, nx=True, ex=ttl_seconds):
                return True
            holder = await self._redis.get(key)
            if isinstance(holder, bytes):
                holder = holder.decode()
            if holder == self._id:
                await self._redis.expire(key, ttl_seconds)
                return True
            return False
        except (RedisError, OSError):
            logger.exception("leader gate unreachable — standing down for %r", name)
            return False


_gate: LeaderGate | None = None


def get_leader_gate() -> LeaderGate:
    global _gate
    if _gate is None:
        url = get_settings().redis_url
        if url:
            import redis.asyncio as aioredis

            _gate = LeaderGate(aioredis.from_url(url))
        else:
            _gate = LeaderGate(None)
    return _gate


def set_leader_gate(gate: LeaderGate | None) -> None:
    """Install a specific gate (tests); None re-derives from settings."""
    global _gate
    _gate = gate
