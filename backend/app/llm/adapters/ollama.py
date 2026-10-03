"""Ollama native API (https://docs.ollama.com/api): /api/chat, /api/tags, /api/show.

Local servers need no credential; Ollama's hosted API (https://ollama.com)
takes a Bearer key. Model capabilities come from /api/show, never guessed.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from app.llm.adapters.base import (
    LLMAdapter,
    as_count,
    as_positive,
    health_from_error,
    parse_arguments,
)
from app.llm.errors import (
    LLMError,
    MalformedResponseError,
    ToolCallingError,
    UnsupportedCapabilityError,
    from_stream_payload,
)
from app.llm.http import Connection, request_json, stream_lines
from app.llm.types import (
    FinishReason,
    HealthStatus,
    LLMCapabilities,
    LLMRequest,
    LLMResponse,
    ModelInfo,
    Role,
    StreamEvent,
    StreamEventType,
    ToolCall,
    Usage,
)

MAX_SHOW_MODELS = 64
_CAPS = {
    "completion": "chat",
    "tools": "tool_calling",
    "vision": "vision",
    "thinking": "reasoning",
    "embedding": "embeddings",
}


class OllamaAdapter(LLMAdapter):
    reasoning_effort_param = True  # `think` accepts low|medium|high on supporting models

    def headers(self, conn: Connection) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if conn.credential:
            headers["Authorization"] = f"Bearer {conn.credential}"
        return headers

    def convert_messages(self, request: LLMRequest) -> list[dict[str, Any]]:
        names_by_call = {c.id: c.name for m in request.messages for c in m.tool_calls}
        out: list[dict[str, Any]] = []
        system = request.system_text()
        if system:
            out.append({"role": "system", "content": system})
        for message in request.messages:
            if message.role == Role.SYSTEM:
                continue
            item: dict[str, Any] = {"role": message.role.value, "content": message.text()}
            images = message.images()
            if images:
                item["images"] = [image.data for image in images]
            if message.tool_calls:
                item["tool_calls"] = [
                    {"function": {"name": c.name, "arguments": c.arguments}}
                    for c in message.tool_calls
                ]
            if message.role == Role.TOOL:
                name = message.name or names_by_call.get(message.tool_call_id or "")
                if name:
                    item["tool_name"] = name
            out.append(item)
        return out

    def build_payload(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        if request.tool_choice is not None and request.tool_choice.mode != "auto":
            raise UnsupportedCapabilityError(
                "Ollama does not support forcing or disabling tool choice", provider=self.provider
            )
        if request.parallel_tool_calls is False:
            raise UnsupportedCapabilityError(
                "Ollama cannot disable parallel tool calls", provider=self.provider
            )
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": self.convert_messages(request),
            "stream": stream,
        }
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if request.stop:
            options["stop"] = request.stop
        if options:
            payload["options"] = options
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
        rf = request.response_format
        if rf is not None and rf.type == "json_object":
            payload["format"] = "json"
        elif rf is not None and rf.type == "json_schema":
            payload["format"] = rf.json_schema
        if request.reasoning_effort is not None:
            payload["think"] = request.reasoning_effort
        return payload

    def _calls(self, message: dict[str, Any], start: int) -> list[ToolCall]:
        calls = []
        for i, raw in enumerate(message.get("tool_calls") or []):
            function = raw.get("function") if isinstance(raw, dict) else None
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise ToolCallingError(provider=self.provider)
            call_id = raw.get("id") if isinstance(raw.get("id"), str) and raw["id"] else None
            calls.append(
                ToolCall(
                    id=call_id or f"call_{start + i}",
                    name=function["name"],
                    arguments=parse_arguments(function.get("arguments"), self.provider),
                )
            )
        return calls

    @staticmethod
    def _finish(reason: Any, has_calls: bool) -> FinishReason:
        if reason == "length":
            return FinishReason.LENGTH
        if has_calls:
            return FinishReason.TOOL_CALLS
        return FinishReason.STOP if reason in ("stop", None) else FinishReason.OTHER

    @staticmethod
    def _usage(data: dict[str, Any]) -> Usage:
        return Usage(
            prompt_tokens=as_count(data.get("prompt_eval_count")),
            completion_tokens=as_count(data.get("eval_count")),
        ).with_total()

    async def generate(self, conn: Connection, request: LLMRequest) -> LLMResponse:
        self.require_credential(conn)
        data = await request_json(
            conn,
            "POST",
            "/api/chat",
            headers=self.headers(conn),
            json_body=self.build_payload(request, stream=False),
        )
        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("content", ""), str):
            raise MalformedResponseError(provider=self.provider)
        calls = self._calls(message, 0)
        thinking = message.get("thinking")
        return LLMResponse(
            content=message.get("content") or "",
            reasoning=thinking if isinstance(thinking, str) and thinking else None,
            tool_calls=calls,
            finish_reason=self._finish(data.get("done_reason"), bool(calls)),
            provider=self.provider,
            model=data.get("model") if isinstance(data.get("model"), str) else request.model,
            usage=self._usage(data),
        )

    async def stream(self, conn: Connection, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        self.require_credential(conn)
        lines = stream_lines(
            conn,
            "POST",
            "/api/chat",
            headers=self.headers(conn),
            json_body=self.build_payload(request, stream=True),
        )
        call_count = 0
        finish: FinishReason | None = None
        usage: Usage | None = None
        async for line in lines:  # NDJSON: one object per line
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except ValueError:
                raise MalformedResponseError(provider=self.provider) from None
            if not isinstance(chunk, dict):
                raise MalformedResponseError(provider=self.provider)
            if "error" in chunk:
                raise from_stream_payload(chunk, provider=self.provider)
            message = chunk.get("message") or {}
            if isinstance(message.get("thinking"), str) and message["thinking"]:
                yield StreamEvent(type=StreamEventType.REASONING, text=message["thinking"])
            if isinstance(message.get("content"), str) and message["content"]:
                yield StreamEvent(type=StreamEventType.TEXT, text=message["content"])
            for call in self._calls(message, call_count):
                call_count += 1
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=call)
            if chunk.get("done"):
                usage = self._usage(chunk)
                finish = self._finish(chunk.get("done_reason"), call_count > 0)
                break
        if usage is not None:
            yield StreamEvent(type=StreamEventType.USAGE, usage=usage)
        yield StreamEvent(type=StreamEventType.FINISH, finish_reason=finish or FinishReason.OTHER)

    async def _tags(self, conn: Connection) -> list[str]:
        data = await request_json(conn, "GET", "/api/tags", headers=self.headers(conn))
        items = data.get("models") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise MalformedResponseError(provider=self.provider)
        return [i["name"] for i in items if isinstance(i, dict) and isinstance(i.get("name"), str)]

    async def _show(self, conn: Connection, name: str) -> ModelInfo:
        info = ModelInfo(provider=self.provider, id=name, display_name=name)
        try:
            data = await request_json(
                conn, "POST", "/api/show", headers=self.headers(conn), json_body={"model": name}
            )
        except LLMError:
            return info  # details unavailable: capabilities stay unknown
        if not isinstance(data, dict):
            return info
        caps = data.get("capabilities")
        if isinstance(caps, list):
            info.capabilities = LLMCapabilities(
                **{field: cap in caps for cap, field in _CAPS.items()}
            )
        model_info = data.get("model_info")
        if isinstance(model_info, dict):
            info.context_window = next(
                (
                    as_positive(v)
                    for k, v in model_info.items()
                    if isinstance(k, str) and k.endswith(".context_length") and as_positive(v)
                ),
                None,
            )
        return info

    async def list_models(self, conn: Connection) -> list[ModelInfo]:
        self.require_credential(conn)
        names = await self._tags(conn)
        slots = asyncio.Semaphore(4)

        async def detail(name: str) -> ModelInfo:
            async with slots:
                return await self._show(conn, name)

        detailed = await asyncio.gather(*(detail(n) for n in names[:MAX_SHOW_MODELS]))
        rest = [
            ModelInfo(provider=self.provider, id=n, display_name=n) for n in names[MAX_SHOW_MODELS:]
        ]
        return list(detailed) + rest

    async def health_check(self, conn: Connection) -> HealthStatus:
        """Cheap liveness: /api/tags only (no per-model /api/show fan-out)."""
        started = time.monotonic()
        try:
            self.require_credential(conn)
            names = await self._tags(conn)
        except LLMError as exc:
            return health_from_error(exc)
        return HealthStatus(
            status="ready",
            latency_ms=int((time.monotonic() - started) * 1000),
            models_available=len(names),
        )
