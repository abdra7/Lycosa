"""Hardened HTTP plumbing shared by every adapter.

- sockets only open to addresses the account's policy permits (netpolicy)
- redirects are never followed and proxy env vars are ignored
- response bodies, stream totals and single lines are size-capped
- httpx log lines are suppressed for the duration of the network call
- transport failures become normalized LLM errors; provider bodies are only
  used to classify an error, never surfaced
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpcore
import httpx

from app.core.logging import provider_request_var
from app.llm.errors import (
    LLMError,
    LLMTimeoutError,
    MalformedResponseError,
    ProviderUnavailableError,
    ResponseTooLargeError,
    from_http,
)
from app.llm.netpolicy import PUBLIC_ONLY, AddressPolicy, GuardedBackend, Resolver

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_STREAM_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Connection:
    """Everything an adapter needs for one call. Built per request by the
    gateway and never cached; the credential is excluded from repr."""

    provider: str
    base_url: str
    credential: str | None = field(default=None, repr=False)
    policy: AddressPolicy = PUBLIC_ONLY
    timeout: float = 120.0
    resolver: Resolver | None = field(default=None, repr=False, compare=False)


def build_client(conn: Connection) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport()
    backend = GuardedBackend(conn.policy, conn.resolver)
    # httpx exposes no network-backend option, so swap in a pool built with
    # the guarded backend (fails closed if httpx internals ever change).
    if not isinstance(getattr(transport, "_pool", None), httpcore.AsyncConnectionPool):
        raise RuntimeError("unsupported httpx version: guarded transport unavailable")
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        http1=True,
        http2=False,
        max_connections=20,
        keepalive_expiry=5.0,
        network_backend=backend,
    )
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(conn.timeout, connect=min(10.0, conn.timeout)),
        follow_redirects=False,
        trust_env=False,  # a proxy would bypass the address guard
    )


@asynccontextmanager
async def _client(conn: Connection) -> AsyncIterator[httpx.AsyncClient]:
    client = build_client(conn)
    try:
        yield client
    finally:
        await client.aclose()


async def _send(
    client: httpx.AsyncClient,
    conn: Connection,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None,
    json_body: Any,
    params: dict[str, Any] | None,
) -> httpx.Response:
    token = provider_request_var.set(True)
    try:
        request = client.build_request(method, url, headers=headers, json=json_body, params=params)
        return await client.send(request, stream=True)
    except httpx.TimeoutException:
        raise LLMTimeoutError(provider=conn.provider) from None
    except httpx.HTTPError:
        raise ProviderUnavailableError(provider=conn.provider) from None
    finally:
        provider_request_var.reset(token)


async def _read_capped(response: httpx.Response, limit: int, provider: str) -> bytes:
    body = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise ResponseTooLargeError(provider=provider)
    except httpx.TimeoutException:
        raise LLMTimeoutError(provider=provider) from None
    except httpx.HTTPError:
        raise ProviderUnavailableError(provider=provider) from None
    return bytes(body)


async def _raise_for_status(response: httpx.Response, provider: str) -> None:
    status = response.status_code
    if status < 300:
        return
    if status < 400:
        raise LLMError(
            "The provider redirected the request; redirects are not followed",
            provider=provider,
            status=status,
        )
    try:
        raw = await _read_capped(response, MAX_ERROR_BODY_BYTES, provider)
        body: Any = json.loads(raw) if raw else None
    except (LLMError, ValueError):
        body = None
    raise from_http(
        status,
        body,
        provider=provider,
        headers={k.lower(): v for k, v in response.headers.items()},
    )


def _url(conn: Connection, path: str) -> str:
    return path if path.startswith(("https://", "http://")) else f"{conn.base_url}{path}"


async def request_json(
    conn: Connection,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> Any:
    async with _client(conn) as client:
        response = await _send(
            client,
            conn,
            method,
            _url(conn, path),
            headers=headers,
            json_body=json_body,
            params=params,
        )
        try:
            await _raise_for_status(response, conn.provider)
            raw = await _read_capped(response, max_bytes, conn.provider)
        finally:
            await response.aclose()
    try:
        return json.loads(raw)
    except ValueError:
        raise MalformedResponseError(provider=conn.provider) from None


async def stream_lines(
    conn: Connection,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Yield decoded lines of a streaming response with per-line and total caps."""
    async with _client(conn) as client:
        response = await _send(
            client,
            conn,
            method,
            _url(conn, path),
            headers=headers,
            json_body=json_body,
            params=params,
        )
        try:
            await _raise_for_status(response, conn.provider)
            total = 0
            buffer = bytearray()
            try:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_STREAM_BYTES:
                        raise ResponseTooLargeError(provider=conn.provider)
                    buffer.extend(chunk)
                    while (newline := buffer.find(b"\n")) >= 0:
                        line = bytes(buffer[:newline]).rstrip(b"\r")
                        del buffer[: newline + 1]
                        yield line.decode("utf-8", errors="replace")
                    if len(buffer) > MAX_LINE_BYTES:
                        raise ResponseTooLargeError(provider=conn.provider)
            except httpx.TimeoutException:
                raise LLMTimeoutError(provider=conn.provider) from None
            except httpx.HTTPError:
                raise ProviderUnavailableError(provider=conn.provider) from None
            if buffer:
                yield bytes(buffer).rstrip(b"\r").decode("utf-8", errors="replace")
        finally:
            await response.aclose()


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str | None, str]]:
    """Minimal server-sent-events parser: yields (event, data) per message."""
    event: str | None = None
    data: list[str] = []
    async for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = None, []
            continue
        if line.startswith(":"):
            continue  # comment / keep-alive
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if data:
        yield event, "\n".join(data)


def loads_event(data: str, provider: str) -> Any:
    try:
        return json.loads(data)
    except ValueError:
        raise MalformedResponseError(provider=provider) from None
