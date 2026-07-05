"""In-process event bus feeding the /api/v1/events WebSocket (ADR-016).

Single-process by design (same scope as ADR-008 rate limiting): services
publish, each connected WebSocket gets its own bounded queue. Slow consumers
drop events rather than block the fabric.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

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

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        event = {
            "type": event_type,
            "ts": datetime.now(UTC).isoformat(),
            "data": data or {},
        }
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("event dropped for slow subscriber: %s", event_type)


_bus = EventBus()


def get_event_bus() -> EventBus:
    return _bus
