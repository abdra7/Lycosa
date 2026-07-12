"""Event bus feeding the /api/v1/events WebSocket (ADR-016; cross-worker
delivery added in ADR-028).

Services publish; each connected WebSocket gets its own bounded queue. Slow
consumers drop events rather than block the fabric.

Default (`REDIS_URL` unset): the original in-process bus — fine with a single
worker. With `REDIS_URL` set, `RedisEventBus` relays every publish through a
Redis pub/sub channel and each worker's listener fans incoming events out to
its local WebSocket queues, so an event published on worker B reaches a
dashboard connected to worker A. If Redis is unreachable a publish degrades to
local-only delivery (this worker's clients still see it) and is logged.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger("lycosa.events")

QUEUE_SIZE = 200


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    @staticmethod
    def _make_event(event_type: str, data: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "type": event_type,
            "ts": datetime.now(UTC).isoformat(),
            "data": data or {},
        }

    def dispatch(self, event: dict[str, Any]) -> None:
        """Fan a ready-made event out to this process's subscriber queues."""
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("event dropped for slow subscriber: %s", event["type"])

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.dispatch(self._make_event(event_type, data))


class RedisEventBus(EventBus):
    """Same interface; delivery goes through one Redis pub/sub channel so all
    workers' WebSocket clients see every event exactly once (via the relay —
    the publisher's own clients get it from its listener too)."""

    CHANNEL = "lycosa:events"

    def __init__(self, client) -> None:  # redis.asyncio.Redis (or fakeredis)
        super().__init__()
        self._redis = client
        self._listener: asyncio.Task | None = None
        self._pending: set[asyncio.Task] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        self._ensure_listener()
        return super().subscribe()

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        event = self._make_event(event_type, data)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # no loop (sync context): deliver locally rather than lose it
            self.dispatch(event)
            return
        self._ensure_listener()
        task = loop.create_task(self._publish_remote(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _publish_remote(self, event: dict[str, Any]) -> None:
        try:
            await self._redis.publish(self.CHANNEL, json.dumps(event))
        except Exception:
            logger.exception(
                "event relay to redis failed — delivering %r locally only", event["type"]
            )
            self.dispatch(event)

    def _ensure_listener(self) -> None:
        if self._listener is None or self._listener.done():
            self._listener = asyncio.get_running_loop().create_task(self._listen())

    async def _listen(self) -> None:
        while True:
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(self.CHANNEL)
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        self.dispatch(json.loads(message["data"]))
                    except (ValueError, TypeError):
                        logger.warning("dropping malformed event from the redis channel")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("event-bus listener lost redis — reconnecting in 1s")
                await asyncio.sleep(1)

    async def aclose(self) -> None:
        """Stop the listener and let in-flight publishes finish (tests/shutdown)."""
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except (asyncio.CancelledError, Exception):
                pass
            self._listener = None


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Process-wide bus: Redis-relayed when REDIS_URL is set, else in-process."""
    global _bus
    if _bus is None:
        url = get_settings().redis_url
        if url:
            import redis.asyncio as aioredis

            _bus = RedisEventBus(aioredis.from_url(url))
        else:
            _bus = EventBus()
    return _bus


def set_event_bus(bus: EventBus | None) -> None:
    """Install a specific bus (tests); None re-derives from settings."""
    global _bus
    _bus = bus
