"""Consistent error envelope for every non-2xx response.

Shape: {"error": {"code": "<machine_code>", "message": "<human text>", "details": [...]}}
"""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("lycosa.errors")

_STATUS_CODES = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "validation_error",
    status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_error",
}


def error_response(
    status_code: int,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
    code: str | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "code": code or _STATUS_CODES.get(status_code, "error"),
            "message": message,
        }
    }
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    from app.llm.errors import LLMError

    @app.exception_handler(LLMError)
    async def llm_exception_handler(request: Request, exc: LLMError) -> JSONResponse:
        # fixed public message + stable code; provider bodies never reach here
        headers = None
        if exc.retry_after is not None and exc.http_status in (429, 503):
            headers = {"Retry-After": str(int(exc.retry_after))}
        return error_response(exc.http_status, exc.public_message, headers=headers, code=exc.code)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(exc.status_code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        from app.core.phantom_privacy import is_phantom

        if is_phantom(request.url.path):
            return error_response(422, "Invalid Phantom request")
        details = [
            {
                "field": ".".join(str(loc) for loc in err["loc"]),
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        return error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Request validation failed", details
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        from app.core.phantom_privacy import is_phantom

        if is_phantom(request.url.path):
            return error_response(
                500, "Phantom request failed", headers={"Cache-Control": "no-store"}
            )
        # opaque by design: never leak internals to clients
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error")
