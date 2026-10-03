"""OpenAI Chat Completions wire format, shared by every provider that speaks it.

Provider differences (token-limit field name, stream usage option, reasoning
effort, model-list metadata) are class attributes or small overrides in the
subclasses; nothing provider-specific leaks out of this module.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from app.llm.adapters.base import (
    LLMAdapter,
    ToolCallAccumulator,
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
    Message,
    ModelInfo,
    Role,
    StreamEvent,
    StreamEventType,
    ToolCall,
    Usage,
)

_FINISH = {
    "stop": FinishReason.STOP,
    "end_turn": FinishReason.STOP,
    "length": FinishReason.LENGTH,
    "tool_calls": FinishReason.TOOL_CALLS,
    "function_call": FinishReason.TOOL_CALLS,
    "content_filter": FinishReason.CONTENT_FILTER,
}


def map_finish(value: Any) -> FinishReason:
    return _FINISH.get(value, FinishReason.OTHER) if isinstance(value, str) else FinishReason.OTHER


class OpenAICompatibleAdapter(LLMAdapter):
    chat_path = "/chat/completions"
    models_path = "/models"
    max_tokens_field = "max_tokens"
    send_stream_usage = False  # stream_options.include_usage, where documented
    reasoning_effort_param = False

    # --- request translation -----------------------------------------------------

    def headers(self, conn: Connection) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if conn.credential:
            headers["Authorization"] = f"Bearer {conn.credential}"
        return headers

    @staticmethod
    def _content(message: Message) -> str | list[dict[str, Any]]:
        if isinstance(message.content, str):
            return message.content
        parts: list[dict[str, Any]] = []
        for part in message.content:
            if isinstance(part, ImagePart):
                url = f"data:{part.media_type};base64,{part.data}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            else:
                parts.append({"type": "text", "text": part.text})
        return parts

    def convert_messages(self, request: LLMRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        system = request.system_text()
        if system:
            out.append({"role": "system", "content": system})
        for message in request.messages:
            if message.role == Role.SYSTEM:
                continue  # hoisted into the leading system message above
            if message.role == Role.USER:
                out.append({"role": "user", "content": self._content(message)})
            elif message.role == Role.ASSISTANT:
                item: dict[str, Any] = {"role": "assistant", "content": message.text()}
                if message.tool_calls:
                    item["content"] = message.text() or None
                    item["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in message.tool_calls
                    ]
                out.append(item)
            else:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.text(),
                    }
                )
        return out

    def build_payload(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": self.convert_messages(request),
            "stream": stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload[self.max_tokens_field] = request.max_tokens
        if request.stop:
            payload["stop"] = request.stop
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]
        if request.tool_choice is not None:
            choice = request.tool_choice
            payload["tool_choice"] = (
                {"type": "function", "function": {"name": choice.name}}
                if choice.mode == "tool"
                else choice.mode
            )
        if request.parallel_tool_calls is not None and request.tools:
            payload["parallel_tool_calls"] = request.parallel_tool_calls
        rf = request.response_format
        if rf is not None and rf.type == "json_object":
            payload["response_format"] = {"type": "json_object"}
        elif rf is not None and rf.type == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": rf.name, "schema": rf.json_schema, "strict": rf.strict},
            }
        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort
        if stream and self.send_stream_usage:
            payload["stream_options"] = {"include_usage": True}
        return payload

    # --- response translation ----------------------------------------------------

    def parse_usage(self, raw: Any) -> Usage:
        if not isinstance(raw, dict):
            return Usage()
        details = raw.get("completion_tokens_details")
        reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
        return Usage(
            prompt_tokens=as_count(raw.get("prompt_tokens")),
            completion_tokens=as_count(raw.get("completion_tokens")),
            total_tokens=as_count(raw.get("total_tokens")),
            reasoning_tokens=as_count(reasoning),
        ).with_total()

    @staticmethod
    def _text(value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, list):  # some servers return content parts
            return "".join(
                p.get("text", "") for p in value if isinstance(p, dict) and p.get("type") == "text"
            )
        return None

    def parse_response(self, data: Any, request: LLMRequest) -> LLMResponse:
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError, AttributeError):
            raise MalformedResponseError(provider=self.provider) from None
        content = self._text(message.get("content"))
        if content is None and message.get("content") is not None:
            raise MalformedResponseError(provider=self.provider)
        calls = []
        for i, raw in enumerate(message.get("tool_calls") or []):
            function = raw.get("function") if isinstance(raw, dict) else None
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise MalformedResponseError(provider=self.provider)
            calls.append(
                ToolCall(
                    id=raw.get("id") or f"call_{i}",
                    name=function["name"],
                    arguments=parse_arguments(function.get("arguments"), self.provider),
                )
            )
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        metadata = {"response_id": data["id"]} if isinstance(data.get("id"), str) else {}
        return LLMResponse(
            content=content or "",
            reasoning=reasoning if isinstance(reasoning, str) and reasoning else None,
            tool_calls=calls,
            finish_reason=map_finish(choice.get("finish_reason")),
            provider=self.provider,
            model=data.get("model") if isinstance(data.get("model"), str) else request.model,
            usage=self.parse_usage(data.get("usage")),
            metadata=metadata,
        )

    # --- interface -----------------------------------------------------------------

    async def generate(self, conn: Connection, request: LLMRequest) -> LLMResponse:
        self.require_credential(conn)
        data = await request_json(
            conn,
            "POST",
            self.chat_path,
            headers=self.headers(conn),
            json_body=self.build_payload(request, stream=False),
        )
        return self.parse_response(data, request)

    async def stream(self, conn: Connection, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        self.require_credential(conn)
        lines = stream_lines(
            conn,
            "POST",
            self.chat_path,
            headers={**self.headers(conn), "Accept": "text/event-stream"},
            json_body=self.build_payload(request, stream=True),
        )
        calls = ToolCallAccumulator(self.provider)
        finish: FinishReason | None = None
        usage: Usage | None = None
        async for _event, data in iter_sse(lines):
            if data.strip() == "[DONE]":
                break
            chunk = loads_event(data, self.provider)
            if not isinstance(chunk, dict):
                raise MalformedResponseError(provider=self.provider)
            if "error" in chunk:
                raise from_stream_payload(chunk, provider=self.provider)
            if chunk.get("usage"):
                usage = self.parse_usage(chunk["usage"])
            for choice in chunk.get("choices") or []:
                if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                text = self._text(delta.get("content"))
                if text:
                    yield StreamEvent(type=StreamEventType.TEXT, text=text)
                thought = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(thought, str) and thought:
                    yield StreamEvent(type=StreamEventType.REASONING, text=thought)
                for raw in delta.get("tool_calls") or []:
                    if not isinstance(raw, dict):
                        continue
                    function = raw.get("function") or {}
                    index = raw.get("index", 0) if isinstance(raw.get("index"), int) else 0
                    calls.add(index, raw.get("id"), function.get("name"), function.get("arguments"))
                    yield StreamEvent(
                        type=StreamEventType.TOOL_CALL_DELTA,
                        index=index,
                        tool_call_id=raw.get("id"),
                        tool_name=function.get("name"),
                        arguments_delta=function.get("arguments"),
                    )
                if choice.get("finish_reason"):
                    finish = map_finish(choice["finish_reason"])
        for call in calls.complete():
            yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=call)
        if usage is not None:
            yield StreamEvent(type=StreamEventType.USAGE, usage=usage)
        yield StreamEvent(type=StreamEventType.FINISH, finish_reason=finish or FinishReason.OTHER)

    # --- model discovery -------------------------------------------------------

    def model_capabilities(self, item: dict[str, Any]) -> LLMCapabilities:
        return LLMCapabilities()

    def model_info(self, item: dict[str, Any]) -> ModelInfo:
        context = next(
            (
                as_positive(item.get(key))
                for key in (
                    "context_length",
                    "context_window",
                    "max_context_length",
                    "max_model_len",
                )
                if as_positive(item.get(key))
            ),
            None,
        )
        top = as_dict(item.get("top_provider"))
        name = item.get("name") or item.get("display_name")
        return ModelInfo(
            provider=self.provider,
            id=item["id"],
            display_name=name if isinstance(name, str) else None,
            context_window=context,
            max_output_tokens=as_positive(item.get("max_output_tokens"))
            or as_positive(top.get("max_completion_tokens")),
            capabilities=self.model_capabilities(item),
        )

    async def list_models(self, conn: Connection) -> list[ModelInfo]:
        self.require_credential(conn)
        data = await request_json(conn, "GET", self.models_path, headers=self.headers(conn))
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise MalformedResponseError(provider=self.provider)
        return [
            self.model_info(item)
            for item in items
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
        ]


# --- providers that speak the format, with their documented differences ----------


class OpenAIAdapter(OpenAICompatibleAdapter):
    # max_tokens is deprecated and rejected by o-series models
    max_tokens_field = "max_completion_tokens"
    send_stream_usage = True
    reasoning_effort_param = True


class DeepSeekAdapter(OpenAICompatibleAdapter):
    send_stream_usage = True
    reasoning_effort_param = True

    def model_capabilities(self, item: dict[str, Any]) -> LLMCapabilities:
        modalities = item.get("input_modalities")
        vision = "image" in modalities if isinstance(modalities, list) else None
        return LLMCapabilities(vision=vision)


class QwenAdapter(OpenAICompatibleAdapter):
    send_stream_usage = True


class XAIAdapter(OpenAICompatibleAdapter):
    max_tokens_field = "max_completion_tokens"
    send_stream_usage = True
    reasoning_effort_param = True


class MistralAdapter(OpenAICompatibleAdapter):
    def model_capabilities(self, item: dict[str, Any]) -> LLMCapabilities:
        caps = as_dict(item.get("capabilities"))
        return LLMCapabilities(
            chat=as_bool(caps.get("completion_chat")),
            tool_calling=as_bool(caps.get("function_calling")),
            vision=as_bool(caps.get("vision")),
        )


class OpenRouterAdapter(OpenAICompatibleAdapter):
    send_stream_usage = True
    reasoning_effort_param = True

    def parse_usage(self, raw: Any) -> Usage:
        usage = super().parse_usage(raw)
        cost = raw.get("cost") if isinstance(raw, dict) else None
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
            usage = usage.model_copy(update={"reported_cost": float(cost)})
        return usage

    def model_capabilities(self, item: dict[str, Any]) -> LLMCapabilities:
        params = item.get("supported_parameters")
        arch = as_dict(item.get("architecture"))
        modalities = arch.get("input_modalities")
        has = (lambda p: p in params) if isinstance(params, list) else (lambda p: None)
        return LLMCapabilities(
            tool_calling=has("tools"),
            structured_output=has("structured_outputs"),
            json_mode=has("response_format"),
            reasoning=has("reasoning"),
            vision="image" in modalities if isinstance(modalities, list) else None,
        )


class LMStudioAdapter(OpenAICompatibleAdapter):
    pass


class VLLMAdapter(OpenAICompatibleAdapter):
    send_stream_usage = True
