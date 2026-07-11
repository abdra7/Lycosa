"""In-process sliding-window rate limiter (ADR-008, keying revised in ADR-020).

Keyed strictly by client IP. A presented ``X-API-Key`` is deliberately NOT part
of the key: keying on the raw header let a caller rotate/forge it to spawn a
fresh bucket per request and escape the limit (F-2). At LAN scope each node has
its own IP, so an IP bucket still gives per-node budgets. Single-process by
design — swap for a Redis backend (and add X-Forwarded-For handling behind a
trusted proxy) when the API scales horizontally.
"""

import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.core.errors import error_response

_EXEMPT_PATHS = {"/healthz", "/docs", "/openapi.json"}

# Module-level so it can be reset between tests (the app/middleware is a
# process-wide singleton). Maps bucket key -> monotonic hit timestamps.
_hits: dict[str, deque[float]] = {}


def reset_rate_limit() -> None:
    """Clear all rate-limit buckets (test isolation)."""
    _hits.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    @staticmethod
    def _client_key(request: Request) -> str:
        # IP only: a forged/rotating X-API-Key header must not create a new
        # bucket that escapes the limit (F-2 / ADR-020).
        return f"ip:{request.client.host if request.client else 'unknown'}"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if not settings.rate_limit_enabled or request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        now = time.monotonic()
        window_start = now - settings.rate_limit_window_seconds
        key = self._client_key(request)

        hits = _hits.setdefault(key, deque())
        while hits and hits[0] < window_start:
            hits.popleft()

        if len(hits) >= settings.rate_limit_requests:
            retry_after = max(1, int(hits[0] - window_start) + 1)
            return error_response(
                429,
                "Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)
        return await call_next(request)
