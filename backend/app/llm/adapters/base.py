"""Adapter contract. One adapter instance per provider; credentials arrive per
call inside a `Connection` and are never stored on the adapter."""

import json
import math
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from app.llm.errors import (
    AuthenticationError,
    CredentialUnavailableError,
    LLMError,
    LLMTimeoutError,
    ModelNotFoundError,
    PermissionDeniedError,
    ProviderUnavailableError,
    QuotaExceededError,
    ToolCallingError,
    UnsupportedCapabilityError,
)
from app.llm.http import Connection
from app.llm.spec import ProviderSpec
from app.llm.types import (
    FinishReason,
    HealthStatus,
    LLMCapabilities,
    LLMRequest,
    LLMResponse,
    ModelInfo,
    StreamEvent,
    StreamEventType,
    ToolCall,
    Usage,
)


def as_count(value: Any) -> int | None:
    """A token count from provider JSON: non-negative finite int, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return int(value)


def as_positive(value: Any) -> int | None:
    count = as_count(value)
    return count if count else None


def as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def as_dict(value: Any) -> dict[str, Any]:
    """A nested JSON object, or {} when the provider sent something else."""
    return value if isinstance(value, dict) else {}


def parse_arguments(raw: Any, provider: str) -> dict[str, Any]:
    """Tool-call arguments as a JSON object; anything else is a malformed call."""
    if isinstance(raw, dict):
        return raw
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise ToolCallingError(provider=provider) from None
        if isinstance(parsed, dict):
            return parsed
    raise ToolCallingError(provider=provider)


class ToolCallAccumulator:
    """Assembles streamed tool-call fragments (keyed by index) into calls."""

    def __init__(self, provider: str) -> None:
        self._provider = provider
        self._calls: dict[int, dict[str, Any]] = {}

    def add(
        self,
        index: int,
        call_id: str | None = None,
        name: str | None = None,
        arguments: str | None = None,
    ) -> None:
        call = self._calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
        if call_id:
            call["id"] = call_id
        if name:
            call["name"] += name if call["name"] != name else ""
        if arguments:
            call["arguments"] += arguments
            if len(call["arguments"]) > 1_000_000:
                raise ToolCallingError(provider=self._provider)

    def __bool__(self) -> bool:
        return bool(self._calls)

    def complete(self) -> list[ToolCall]:
        calls = []
        for index in sorted(self._calls):
            call = self._calls[index]
            if not call["name"]:
                raise ToolCallingError(provider=self._provider)
            calls.append(
                ToolCall(
                    id=call["id"] or f"call_{index}",
                    name=call["name"],
                    arguments=parse_arguments(call["arguments"], self._provider),
                )
            )
        return calls


def health_from_error(exc: LLMError) -> HealthStatus:
    if isinstance(exc, (AuthenticationError, PermissionDeniedError, QuotaExceededError)):
        return HealthStatus(status="unauthorized", detail=exc.public_message)
    if isinstance(exc, (LLMTimeoutError, ProviderUnavailableError)):
        return HealthStatus(status="offline", detail=exc.public_message)
    if isinstance(exc, ModelNotFoundError):
        return HealthStatus(
            status="unknown",
            detail="The endpoint does not offer model listing; send a test prompt instead",
        )
    return HealthStatus(status="error", detail=exc.public_message)


async def collect(events: AsyncIterator[StreamEvent], *, provider: str, model: str) -> LLMResponse:
    """Fold a normalized event stream into one response."""
    text: list[str] = []
    reasoning: list[str] = []
    calls: list[ToolCall] = []
    usage = Usage()
    finish = FinishReason.OTHER
    async for event in events:
        if event.type == StreamEventType.TEXT and event.text:
            text.append(event.text)
        elif event.type == StreamEventType.REASONING and event.text:
            reasoning.append(event.text)
        elif event.type == StreamEventType.TOOL_CALL and event.tool_call:
            calls.append(event.tool_call)
        elif event.type == StreamEventType.USAGE and event.usage:
            usage = event.usage
        elif event.type == StreamEventType.FINISH and event.finish_reason:
            finish = event.finish_reason
    return LLMResponse(
        content="".join(text),
        reasoning="".join(reasoning) or None,
        tool_calls=calls,
        finish_reason=finish,
        provider=provider,
        model=model,
        usage=usage,
    )


class LLMAdapter(ABC):
    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec

    @property
    def provider(self) -> str:
        return self.spec.id

    # --- the normalized interface -------------------------------------------------

    @abstractmethod
    async def generate(self, conn: Connection, request: LLMRequest) -> LLMResponse: ...

    @abstractmethod
    def stream(self, conn: Connection, request: LLMRequest) -> AsyncIterator[StreamEvent]: ...

    @abstractmethod
    async def list_models(self, conn: Connection) -> list[ModelInfo]: ...

    async def health_check(self, conn: Connection) -> HealthStatus:
        """A real, authenticated, token-free call (model listing by default)."""
        started = time.monotonic()
        try:
            models = await self.list_models(conn)
        except LLMError as exc:
            return health_from_error(exc)
        return HealthStatus(
            status="ready",
            latency_ms=int((time.monotonic() - started) * 1000),
            models_available=len(models),
        )

    def capabilities(self, model: ModelInfo | None = None) -> LLMCapabilities:
        return self.spec.capabilities.merged_with(model.capabilities if model else None)

    # --- shared checks ------------------------------------------------------------

    reasoning_effort_param = False  # adapter knows how to send a reasoning effort

    def check_request(self, request: LLMRequest, model: ModelInfo | None = None) -> None:
        """Refuse features the provider/model is known not to support. Unknown
        support is attempted; the provider's own error is then normalized."""
        caps = self.capabilities(model)

        def refuse(feature: str) -> None:
            raise UnsupportedCapabilityError(
                f"{self.spec.display_name} does not support {feature} for this model",
                provider=self.provider,
            )

        if request.tools and caps.tool_calling is False:
            refuse("tool calling")
        if request.parallel_tool_calls and caps.parallel_tools is False:
            refuse("parallel tool calls")
        if request.uses_vision() and caps.vision is False:
            refuse("image input")
        rf = request.response_format
        if rf is not None and rf.type == "json_schema" and caps.structured_output is False:
            refuse("structured (JSON schema) output")
        if rf is not None and rf.type == "json_object" and caps.json_mode is False:
            refuse("JSON object mode")
        if request.reasoning_effort and (
            caps.reasoning is False or not self.reasoning_effort_param
        ):
            refuse("a reasoning effort setting")

    def require_credential(self, conn: Connection) -> None:
        if self.spec.credential_required and not conn.credential:
            raise CredentialUnavailableError(provider=self.provider)
