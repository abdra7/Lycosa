"""In-process sliding-window rate limiter (ADR-008).

Keyed by API key when presented, else client IP. Single-process by design —
good for the one-container controller; swap for a Redis backend when the API
scales horizontally.
"""

import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.core.errors import error_response
from app.core.security import API_KEY_HEADER

_EXEMPT_PATHS = {"/healthz", "/docs", "/openapi.json"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:  # noqa: ANN001
        super().__init__(app)
        self._hits: dict[str, deque[float]] = {}

    @staticmethod
    def _client_key(request: Request) -> str:
        api_key = request.headers.get(API_KEY_HEADER)
        if api_key:
            return f"key:{api_key[:16]}"
        return f"ip:{request.client.host if request.client else 'unknown'}"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if not settings.rate_limit_enabled or request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        now = time.monotonic()
        window_start = now - settings.rate_limit_window_seconds
        key = self._client_key(request)

        hits = self._hits.setdefault(key, deque())
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
