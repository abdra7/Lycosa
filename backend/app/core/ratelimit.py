"""Sliding-window rate limiter (ADR-008; keying revised in ADR-020; state
moved behind the window store in ADR-027).

Keyed strictly by client IP. A presented ``X-API-Key`` is deliberately NOT part
of the key: keying on the raw header let a caller rotate/forge it to spawn a
fresh bucket per request and escape the limit (F-2). At LAN scope each node has
its own IP, so an IP bucket still gives per-node budgets.

Bucket state lives in the window store: per-process by default, shared across
uvicorn workers when REDIS_URL is set. If the shared store is unreachable the
limiter fails OPEN (admit + log) — a Redis blip must not turn every API request
into a 5xx; the login guard is the one that fails closed (ADR-027).
"""

import logging

from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.clientip import client_ip
from app.core.config import get_settings
from app.core.errors import error_response
from app.core.window_store import InProcessWindowStore, get_window_store

logger = logging.getLogger("lycosa.ratelimit")

_EXEMPT_PATHS = {"/healthz", "/docs", "/openapi.json"}

_KEY_PREFIX = "rl:"


def reset_rate_limit() -> None:
    """Clear all in-process rate-limit buckets (test isolation). Tests that
    install a Redis-backed store manage its lifetime themselves."""
    store = get_window_store()
    if isinstance(store, InProcessWindowStore):
        store.clear_prefix(_KEY_PREFIX)


class RateLimitMiddleware(BaseHTTPMiddleware):
    @staticmethod
    def _client_key(request: Request) -> str:
        # IP only: a forged/rotating X-API-Key header must not create a new
        # bucket that escapes the limit (F-2 / ADR-020). Behind a trusted
        # proxy the forwarded client IP is used instead (ADR-028).
        return f"{_KEY_PREFIX}ip:{client_ip(request) or 'unknown'}"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if not settings.rate_limit_enabled or request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        try:
            retry_after = await get_window_store().try_hit(
                self._client_key(request),
                limit=settings.rate_limit_requests,
                window_seconds=settings.rate_limit_window_seconds,
            )
        except (RedisError, OSError):
            logger.exception("rate-limit store unreachable — admitting request (fail-open)")
            return await call_next(request)

        if retry_after:
            return error_response(
                429,
                "Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
