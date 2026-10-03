"""The one entry point callers use: generate or stream over a route.

For each route entry, in order: capability gate -> connection (credential
decrypted for this call only) -> provider call with bounded retries and
exponential backoff on transient errors -> usage record. Any normalized
failure moves to the next entry (a fallback, logged and counted). A stream
can only fall back before its first event; after that the error is final.
"""

import asyncio
import functools
import json
import logging
import random
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import request_id_var
from app.core.metrics import LLM_FALLBACKS
from app.llm import catalog, discovery, usage
from app.llm.accounts import Actor, connection_for
from app.llm.errors import LLMError, LLMTimeoutError, NoRouteError
from app.llm.routing import Target
from app.llm.types import (
    LLMRequest,
    LLMResponse,
    StreamEvent,
    StreamEventType,
    Usage,
)

logger = logging.getLogger("lycosa.llm")

MAX_BACKOFF_SECONDS = 20.0
T = TypeVar("T")

# seam for tests: retries must not actually wait there
sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


@dataclass
class CallContext:
    actor: Actor
    purpose: str | None = None
    task_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
    requires_privacy: bool = False
    request_id: str = field(default_factory=lambda: request_id_var.get() or uuid.uuid4().hex[:16])


@dataclass
class GatewayResult:
    response: LLMResponse
    target: Target
    attempts: int
    fallback_index: int
    latency_ms: int
    estimated_cost: float | None
    cost_source: str | None
    skipped: list[dict[str, str]]


def backoff_delay(attempt: int, retry_after: float | None) -> float:
    base = 0.5 * (2**attempt)
    delay = base + random.uniform(0, base / 2)
    if retry_after is not None:
        delay = max(delay, retry_after)
    return min(delay, MAX_BACKOFF_SECONDS)


async def with_retries(call: Callable[[], Awaitable[T]], *, deadline: float) -> tuple[T, int]:
    """Run `call`, retrying only retryable errors. Returns (result, attempts)."""
    max_retries = get_settings().llm_max_retries
    attempt = 0
    while True:
        attempt += 1
        try:
            async with asyncio.timeout(deadline):
                return await call(), attempt
        except TimeoutError:
            error: LLMError = LLMTimeoutError()
        except LLMError as exc:
            error = exc
        error.attempts = attempt  # type: ignore[attr-defined]
        if not error.retryable or attempt > max_retries:
            raise error
        await sleep(backoff_delay(attempt - 1, error.retry_after))


def _redact_text(text: str | None, secret: str | None) -> str | None:
    if text is None or not secret or len(secret) < 8:
        return text
    return text.replace(secret, "[REDACTED]")


def _redact(response: LLMResponse, secret: str | None) -> LLMResponse:
    """Providers occasionally echo input; a key must never flow back out."""
    if not secret or len(secret) < 8:
        return response
    calls = []
    for call in response.tool_calls:
        raw = json.dumps(call.arguments)
        if secret in raw:
            call = call.model_copy(
                update={"arguments": json.loads(raw.replace(secret, "[REDACTED]"))}
            )
        calls.append(call)
    return response.model_copy(
        update={
            "content": _redact_text(response.content, secret),
            "reasoning": _redact_text(response.reasoning, secret),
            "tool_calls": calls,
        }
    )


def _note_fallback(
    ctx: CallContext, target: Target, index: int, total: int, error: LLMError
) -> None:
    if index + 1 < total:
        LLM_FALLBACKS.labels(target.account.provider).inc()
        logger.warning(
            "llm fallback after %s",
            error.code,
            extra={
                "llm_request_id": ctx.request_id,
                "provider": target.account.provider,
                "account_id": str(target.account.id),
                "model": target.model,
                "error_code": error.code,
                "fallback_index": index,
            },
        )


async def _record(
    db: AsyncSession,
    ctx: CallContext,
    target: Target,
    *,
    status: str,
    error: LLMError | None,
    attempts: int,
    index: int,
    started: float,
    stream: bool,
    result_usage: Usage | None,
) -> tuple[float | None, str | None, int]:
    latency_ms = int((time.monotonic() - started) * 1000)
    cost, source = await usage.record(
        db,
        request_id=ctx.request_id,
        user_id=ctx.actor.user_id,
        api_key_id=ctx.actor.api_key_id,
        account_id=target.account.id,
        provider=target.account.provider,
        model=target.model,
        purpose=ctx.purpose,
        task_id=ctx.task_id,
        workflow_run_id=ctx.workflow_run_id,
        status=status,
        error_code=error.code if error else None,
        attempts=attempts,
        fallback_index=index,
        stream=stream,
        latency_ms=latency_ms,
        usage=result_usage,
    )
    return cost, source, latency_ms


async def generate(
    db: AsyncSession, ctx: CallContext, request: LLMRequest, targets: list[Target]
) -> GatewayResult:
    if not targets:
        raise NoRouteError()
    deadline = float(get_settings().llm_request_timeout_seconds)
    skipped: list[dict[str, str]] = []
    last_error: LLMError = NoRouteError()
    for index, target in enumerate(targets):
        adapter = catalog.get(target.account.provider)
        call_request = request.model_copy(update={"model": target.model})
        started = time.monotonic()
        attempts = 0
        credential: str | None = None
        try:
            adapter.check_request(
                call_request, discovery.cached_model(target.account.id, target.model)
            )
            conn = await connection_for(db, target.account, private_only=ctx.requires_privacy)
            credential = conn.credential
            response, attempts = await with_retries(
                functools.partial(adapter.generate, conn, call_request), deadline=deadline
            )
        except LLMError as exc:
            attempts = getattr(exc, "attempts", attempts)
            await _record(
                db,
                ctx,
                target,
                status="failed",
                error=exc,
                attempts=attempts,
                index=index,
                started=started,
                stream=False,
                result_usage=None,
            )
            skipped.append(
                {
                    "provider": target.account.provider,
                    "account_id": str(target.account.id),
                    "error": exc.code,
                }
            )
            _note_fallback(ctx, target, index, len(targets), exc)
            last_error = exc
            continue
        response = _redact(response, credential)
        credential = None
        cost, source, latency_ms = await _record(
            db,
            ctx,
            target,
            status="succeeded",
            error=None,
            attempts=attempts,
            index=index,
            started=started,
            stream=False,
            result_usage=response.usage,
        )
        response.metadata.update(
            {"account_id": str(target.account.id), "request_id": ctx.request_id}
        )
        return GatewayResult(
            response=response,
            target=target,
            attempts=attempts,
            fallback_index=index,
            latency_ms=latency_ms,
            estimated_cost=cost,
            cost_source=source,
            skipped=skipped,
        )
    last_error.skipped = skipped  # type: ignore[attr-defined]
    raise last_error


async def stream(
    db: AsyncSession, ctx: CallContext, request: LLMRequest, targets: list[Target]
) -> AsyncIterator[StreamEvent]:
    """Normalized stream with retry/fallback until the first event is out."""
    if not targets:
        raise NoRouteError()
    max_retries = get_settings().llm_max_retries
    last_error: LLMError = NoRouteError()
    for index, target in enumerate(targets):
        adapter = catalog.get(target.account.provider)
        call_request = request.model_copy(update={"model": target.model})
        started = time.monotonic()
        attempts = 0
        try:
            adapter.check_request(
                call_request, discovery.cached_model(target.account.id, target.model)
            )
            conn = await connection_for(db, target.account, private_only=ctx.requires_privacy)
        except LLMError as exc:
            await _record(
                db,
                ctx,
                target,
                status="failed",
                error=exc,
                attempts=0,
                index=index,
                started=started,
                stream=True,
                result_usage=None,
            )
            _note_fallback(ctx, target, index, len(targets), exc)
            last_error = exc
            continue
        secret = conn.credential
        while True:
            attempts += 1
            emitted = False
            final_usage: Usage | None = None
            try:
                async for event in adapter.stream(conn, call_request):
                    if not emitted:
                        emitted = True
                        yield StreamEvent(
                            type=StreamEventType.START,
                            metadata={
                                "provider": target.account.provider,
                                "account_id": str(target.account.id),
                                "model": target.model,
                                "fallback_index": index,
                                "request_id": ctx.request_id,
                            },
                        )
                    if event.type == StreamEventType.USAGE:
                        final_usage = event.usage
                    if event.text:
                        event = event.model_copy(update={"text": _redact_text(event.text, secret)})
                    yield event
            except LLMError as exc:
                if not emitted and exc.retryable and attempts <= max_retries:
                    await sleep(backoff_delay(attempts - 1, exc.retry_after))
                    continue
                await _record(
                    db,
                    ctx,
                    target,
                    status="failed",
                    error=exc,
                    attempts=attempts,
                    index=index,
                    started=started,
                    stream=True,
                    result_usage=final_usage,
                )
                if emitted:
                    raise  # output already reached the caller: no silent switch
                _note_fallback(ctx, target, index, len(targets), exc)
                last_error = exc
                break
            await _record(
                db,
                ctx,
                target,
                status="succeeded",
                error=None,
                attempts=attempts,
                index=index,
                started=started,
                stream=True,
                result_usage=final_usage,
            )
            return
    raise last_error
