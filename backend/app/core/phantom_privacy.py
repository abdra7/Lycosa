"""Content-safe errors and bounded request bodies on the independent Phantom API."""

import asyncio
from contextvars import ContextVar

from starlette.responses import JSONResponse

phantom_request: ContextVar[bool] = ContextVar("phantom_request", default=False)


def is_phantom(path: str) -> bool:
    return path == "/api/v1/phantom" or path.startswith("/api/v1/phantom/")


class PhantomPrivacyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not is_phantom(scope["path"]):
            return await self.app(scope, receive, send)
        token = phantom_request.set(True)
        started = False
        received = 0

        async def private_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in {b"cache-control", b"pragma", b"x-request-id"}
                ] + [(b"cache-control", b"no-store"), (b"pragma", b"no-cache")]
            await send(message)

        pending = []

        async def private_receive():
            if pending:
                return pending.pop(0)
            return await receive()

        try:
            # Reject query strings so prompts cannot be put into access-log URLs.
            if scope.get("query_string"):
                await JSONResponse(
                    {"error": {"message": "Phantom query parameters forbidden"}}, 400
                )(scope, receive, private_send)
            else:
                # Validate the byte bound before FastAPI parses JSON. Do not put
                # oversized input in a validation exception or traceback.
                async with asyncio.timeout(15):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        received += len(message.get("body", b""))
                        if received > 262144:
                            await JSONResponse(
                                {"error": {"message": "Phantom request too large"}}, 413
                            )(scope, receive, private_send)
                            return
                        pending.append(message)
                        if not message.get("more_body", False):
                            break
                await self.app(scope, private_receive, private_send)
        except Exception:
            if not started:
                await JSONResponse({"error": {"message": "Phantom request failed"}}, 500)(
                    scope, receive, private_send
                )
        finally:
            phantom_request.reset(token)
