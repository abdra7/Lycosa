"""Anthropic Messages API (https://platform.claude.com/docs/en/api/messages)."""

from collections.abc import AsyncIterator
from typing import Any

from app.llm.adapters.base import (
    LLMAdapter,
    as_bool,
    as_count,
    as_dict,
    as_positive,
    parse_arguments,
)
from app.llm.errors import MalformedResponseError, from_stream_payload
from app.llm.http import Connection, iter_sse, loads_event, request_json, stream_lines
from app.llm.types import (
    FinishReason,
    ImagePart,
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

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096  # the API requires max_tokens on every request
MAX_MODEL_PAGES = 5

_STOP = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "max_tokens": FinishReason.LENGTH,
    "tool_use": FinishReason.TOOL_CALLS,
    "refusal": FinishReason.CONTENT_FILTER,
}


def _supported(caps: dict[str, Any], key: str) -> bool | None:
    entry = caps.get(key)
    return as_bool(entry.get("supported")) if isinstance(entry, dict) else None


class AnthropicAdapter(LLMAdapter):
    reasoning_effort_param = True  # output_config.effort

    def headers(self, conn: Connection) -> dict[str, str]:
        return {
            "x-api-key": conn.credential or "",
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

    def convert_messages(self, request: LLMRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []

        def push(role: str, blocks: list[dict[str, Any]]) -> None:
            if not blocks:
                return
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)  # merge consecutive same-role turns
            else:
                out.append({"role": role, "content": blocks})

        for message in request.messages:
            if message.role == Role.SYSTEM:
                continue  # sent as the top-level `system` field
            if message.role == Role.TOOL:
                push(
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.text(),
                        }
                    ],
                )
                continue
            blocks: list[dict[str, Any]] = []
            parts = [message.content] if isinstance(message.content, str) else list(message.content)
            for part in parts:
                if isinstance(part, str):
                    if part:
                        blocks.append({"type": "text", "text": part})
                elif isinstance(part, ImagePart):
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": part.media_type,
                                "data": part.data,
                            },
                        }
                    )
                elif part.text:  # the API rejects empty text blocks
                    blocks.append({"type": "text", "text": part.text})
            for call in message.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                )
            push("assistant" if message.role == Role.ASSISTANT else "user", blocks)
        return out

    def build_payload(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "messages": self.convert_messages(request),
        }
        system = request.system_text()
        if system:
            payload["system"] = system
        # newer models reject any temperature other than the default; only
        # send one when the caller set it explicitly
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.stop:
            payload["stop_sequences"] = request.stop
        if request.tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in request.tools
            ]
            choice: dict[str, Any] | None = None
            if request.tool_choice is not None:
                mode = request.tool_choice.mode
                choice = (
                    {"type": "tool", "name": request.tool_choice.name}
                    if mode == "tool"
                    else {"type": {"auto": "auto", "required": "any", "none": "none"}[mode]}
                )
            if request.parallel_tool_calls is False:
                choice = choice or {"type": "auto"}
                if choice["type"] != "none":
                    choice["disable_parallel_tool_use"] = True
            if choice is not None:
                payload["tool_choice"] = choice
        output_config: dict[str, Any] = {}
        rf = request.response_format
        if rf is not None and rf.type == "json_schema":
            output_config["format"] = {"type": "json_schema", "schema": rf.json_schema}
        if request.reasoning_effort is not None:
            output_config["effort"] = request.reasoning_effort
        if output_config:
            payload["output_config"] = output_config
        if stream:
            payload["stream"] = True
        return payload

    @staticmethod
    def _usage(raw: Any, prior: Usage | None = None) -> Usage:
        if not isinstance(raw, dict):
            return prior or Usage()
        prompt_parts = [
            as_count(raw.get(k))
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        ]
        prompt = (
            sum(p for p in prompt_parts if p is not None)
            if any(p is not None for p in prompt_parts)
            else (prior.prompt_tokens if prior else None)
        )
        completion = as_count(raw.get("output_tokens"))
        if completion is None and prior is not None:
            completion = prior.completion_tokens
        return Usage(prompt_tokens=prompt, completion_tokens=completion).with_total()

    def parse_response(self, data: Any, request: LLMRequest) -> LLMResponse:
        blocks = data.get("content") if isinstance(data, dict) else None
        if not isinstance(blocks, list):
            raise MalformedResponseError(provider=self.provider)
        text, thinking, calls = [], [], []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str):
                text.append(block["text"])
            elif kind == "thinking" and isinstance(block.get("thinking"), str):
                thinking.append(block["thinking"])
            elif kind == "tool_use":
                if not isinstance(block.get("id"), str) or not isinstance(block.get("name"), str):
                    raise MalformedResponseError(provider=self.provider)
                calls.append(
                    ToolCall(
                        id=block["id"],
                        name=block["name"],
                        arguments=parse_arguments(block.get("input"), self.provider),
                    )
                )
        stop = data.get("stop_reason")
        return LLMResponse(
            content="".join(text),
            reasoning="".join(thinking) or None,
            tool_calls=calls,
            finish_reason=_STOP.get(stop, FinishReason.OTHER)
            if isinstance(stop, str)
            else FinishReason.OTHER,
            provider=self.provider,
            model=data.get("model") if isinstance(data.get("model"), str) else request.model,
            usage=self._usage(data.get("usage")),
            metadata={"response_id": data["id"]} if isinstance(data.get("id"), str) else {},
        )

    async def generate(self, conn: Connection, request: LLMRequest) -> LLMResponse:
        self.require_credential(conn)
        data = await request_json(
            conn,
            "POST",
            "/messages",
            headers=self.headers(conn),
            json_body=self.build_payload(request, stream=False),
        )
        return self.parse_response(data, request)

    async def stream(self, conn: Connection, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        self.require_credential(conn)
        lines = stream_lines(
            conn,
            "POST",
            "/messages",
            headers={**self.headers(conn), "accept": "text/event-stream"},
            json_body=self.build_payload(request, stream=True),
        )
        tool_blocks: dict[int, dict[str, Any]] = {}
        usage: Usage | None = None
        finish = FinishReason.OTHER
        async for _event, data in iter_sse(lines):
            payload = loads_event(data, self.provider)
            if not isinstance(payload, dict):
                raise MalformedResponseError(provider=self.provider)
            kind = payload.get("type")
            if kind == "error":
                raise from_stream_payload(payload, provider=self.provider)
            if kind == "message_start":
                message = payload.get("message") or {}
                usage = self._usage(message.get("usage"), usage)
            elif kind == "content_block_start":
                block = payload.get("content_block") or {}
                index = payload.get("index")
                if block.get("type") == "tool_use" and isinstance(index, int):
                    tool_blocks[index] = {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "json": "",
                    }
                    yield StreamEvent(
                        type=StreamEventType.TOOL_CALL_DELTA,
                        index=index,
                        tool_call_id=block.get("id"),
                        tool_name=block.get("name"),
                    )
            elif kind == "content_block_delta":
                delta = payload.get("delta") or {}
                dtype = delta.get("type")
                index = payload.get("index")
                if dtype == "text_delta" and delta.get("text"):
                    yield StreamEvent(type=StreamEventType.TEXT, text=delta["text"])
                elif dtype == "thinking_delta" and delta.get("thinking"):
                    yield StreamEvent(type=StreamEventType.REASONING, text=delta["thinking"])
                elif dtype == "input_json_delta" and index in tool_blocks:
                    fragment = delta.get("partial_json") or ""
                    tool_blocks[index]["json"] += fragment
                    yield StreamEvent(
                        type=StreamEventType.TOOL_CALL_DELTA,
                        index=index,
                        tool_call_id=tool_blocks[index]["id"],
                        arguments_delta=fragment,
                    )
            elif kind == "content_block_stop":
                index = payload.get("index")
                block = tool_blocks.get(index) if isinstance(index, int) else None
                if block is not None:
                    if not isinstance(block["id"], str) or not isinstance(block["name"], str):
                        raise MalformedResponseError(provider=self.provider)
                    yield StreamEvent(
                        type=StreamEventType.TOOL_CALL,
                        tool_call=ToolCall(
                            id=block["id"],
                            name=block["name"],
                            arguments=parse_arguments(block["json"], self.provider),
                        ),
                    )
            elif kind == "message_delta":
                stop = (payload.get("delta") or {}).get("stop_reason")
                if isinstance(stop, str):
                    finish = _STOP.get(stop, FinishReason.OTHER)
                usage = self._usage(payload.get("usage"), usage)
            elif kind == "message_stop":
                break
        if usage is not None:
            yield StreamEvent(type=StreamEventType.USAGE, usage=usage)
        yield StreamEvent(type=StreamEventType.FINISH, finish_reason=finish)

    def model_info(self, item: dict[str, Any]) -> ModelInfo:
        caps = as_dict(item.get("capabilities"))
        thinking = as_dict(caps.get("thinking"))
        return ModelInfo(
            provider=self.provider,
            id=item["id"],
            display_name=item.get("display_name")
            if isinstance(item.get("display_name"), str)
            else None,
            context_window=as_positive(item.get("max_input_tokens")),
            max_output_tokens=as_positive(item.get("max_tokens")),
            capabilities=LLMCapabilities(
                chat=True,
                vision=_supported(caps, "image_input"),
                structured_output=_supported(caps, "structured_outputs"),
                reasoning=as_bool(thinking.get("supported")),
            ),
        )

    async def list_models(self, conn: Connection) -> list[ModelInfo]:
        self.require_credential(conn)
        models: list[ModelInfo] = []
        params: dict[str, Any] = {"limit": 1000}
        for _ in range(MAX_MODEL_PAGES):
            data = await request_json(
                conn, "GET", "/models", headers=self.headers(conn), params=params
            )
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise MalformedResponseError(provider=self.provider)
            models += [
                self.model_info(i)
                for i in items
                if isinstance(i, dict) and isinstance(i.get("id"), str)
            ]
            if not data.get("has_more") or not isinstance(data.get("last_id"), str):
                break
            params = {"limit": 1000, "after_id": data["last_id"]}
        return models
