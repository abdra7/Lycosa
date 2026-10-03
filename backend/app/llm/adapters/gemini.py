"""Google Gemini API, native generateContent (https://ai.google.dev/api/generate-content).

The API key travels in the `x-goog-api-key` header, never in the URL, so it
cannot end up in an access log.
"""

import re
from collections.abc import AsyncIterator
from typing import Any

from app.llm.adapters.base import LLMAdapter, as_bool, as_count, as_positive
from app.llm.errors import (
    InvalidRequestError,
    MalformedResponseError,
    ToolCallingError,
    UnsupportedCapabilityError,
    from_stream_payload,
)
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

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_MODEL_PAGES = 5
_FILTERED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}


def _model_path(model: str) -> str:
    model = model.removeprefix("models/")
    if not _MODEL_RE.fullmatch(model):
        raise InvalidRequestError("Invalid Gemini model identifier")
    return f"/models/{model}"


class GeminiAdapter(LLMAdapter):
    def headers(self, conn: Connection) -> dict[str, str]:
        return {"x-goog-api-key": conn.credential or "", "Content-Type": "application/json"}

    def convert_contents(self, request: LLMRequest) -> list[dict[str, Any]]:
        names_by_call = {call.id: call.name for m in request.messages for call in m.tool_calls}
        contents: list[dict[str, Any]] = []

        def push(role: str, parts: list[dict[str, Any]]) -> None:
            if not parts:
                return
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})

        for message in request.messages:
            if message.role == Role.SYSTEM:
                continue
            if message.role == Role.TOOL:
                name = message.name or names_by_call.get(message.tool_call_id or "")
                if not name:
                    raise InvalidRequestError("Gemini tool results need the tool name")
                response: dict[str, Any] = {"name": name, "response": {"result": message.text()}}
                if message.tool_call_id and not message.tool_call_id.startswith("call_"):
                    response["id"] = message.tool_call_id
                push("user", [{"functionResponse": response}])
                continue
            parts: list[dict[str, Any]] = []
            if isinstance(message.content, str):
                if message.content:
                    parts.append({"text": message.content})
            else:
                for part in message.content:
                    if isinstance(part, ImagePart):
                        parts.append(
                            {"inlineData": {"mimeType": part.media_type, "data": part.data}}
                        )
                    elif part.text:
                        parts.append({"text": part.text})
            for call in message.tool_calls:
                function_call: dict[str, Any] = {"name": call.name, "args": call.arguments}
                if not call.id.startswith("call_"):
                    function_call["id"] = call.id
                item: dict[str, Any] = {"functionCall": function_call}
                if call.extra.get("thought_signature"):
                    item["thoughtSignature"] = call.extra["thought_signature"]
                parts.append(item)
            push("model" if message.role == Role.ASSISTANT else "user", parts)
        return contents

    def build_payload(self, request: LLMRequest) -> dict[str, Any]:
        if request.parallel_tool_calls is False:
            # the API offers no switch to disable parallel calls
            raise UnsupportedCapabilityError(
                "Gemini cannot disable parallel tool calls", provider=self.provider
            )
        payload: dict[str, Any] = {"contents": self.convert_contents(request)}
        system = request.system_text()
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        config: dict[str, Any] = {}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.max_tokens is not None:
            config["maxOutputTokens"] = request.max_tokens
        if request.stop:
            config["stopSequences"] = request.stop
        rf = request.response_format
        if rf is not None and rf.type in ("json_object", "json_schema"):
            config["responseMimeType"] = "application/json"
            if rf.type == "json_schema":
                config["responseJsonSchema"] = rf.json_schema
        if config:
            payload["generationConfig"] = config
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parametersJsonSchema": t.parameters,
                        }
                        for t in request.tools
                    ]
                }
            ]
        if request.tool_choice is not None:
            mode = request.tool_choice.mode
            calling: dict[str, Any] = {
                "mode": {"auto": "AUTO", "required": "ANY", "none": "NONE", "tool": "ANY"}[mode]
            }
            if mode == "tool":
                calling["allowedFunctionNames"] = [request.tool_choice.name]
            payload["toolConfig"] = {"functionCallingConfig": calling}
        return payload

    @staticmethod
    def _usage(raw: Any) -> Usage:
        if not isinstance(raw, dict):
            return Usage()
        return Usage(
            prompt_tokens=as_count(raw.get("promptTokenCount")),
            completion_tokens=as_count(raw.get("candidatesTokenCount")),
            total_tokens=as_count(raw.get("totalTokenCount")),
            reasoning_tokens=as_count(raw.get("thoughtsTokenCount")),
        ).with_total()

    def _parts(
        self, candidate: dict[str, Any], start: int
    ) -> tuple[list[str], list[str], list[ToolCall]]:
        content = candidate.get("content") or {}
        text: list[str] = []
        thoughts: list[str] = []
        calls: list[ToolCall] = []
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                (thoughts if part.get("thought") else text).append(part["text"])
            call = part.get("functionCall")
            if isinstance(call, dict):
                if not isinstance(call.get("name"), str):
                    raise ToolCallingError(provider=self.provider)
                args = call.get("args") or {}
                if not isinstance(args, dict):
                    raise ToolCallingError(provider=self.provider)
                extra = {}
                if isinstance(part.get("thoughtSignature"), str):
                    extra["thought_signature"] = part["thoughtSignature"]
                calls.append(
                    ToolCall(
                        id=call["id"]
                        if isinstance(call.get("id"), str) and call["id"]
                        else f"call_{start + len(calls)}",
                        name=call["name"],
                        arguments=args,
                        extra=extra,
                    )
                )
        return text, thoughts, calls

    def _finish(self, reason: Any, has_calls: bool) -> FinishReason:
        if reason == "MALFORMED_FUNCTION_CALL":
            raise ToolCallingError(provider=self.provider)
        if reason == "STOP":
            return FinishReason.TOOL_CALLS if has_calls else FinishReason.STOP
        if reason == "MAX_TOKENS":
            return FinishReason.LENGTH
        if reason in _FILTERED:
            return FinishReason.CONTENT_FILTER
        return FinishReason.OTHER

    def parse_response(self, data: Any, request: LLMRequest) -> LLMResponse:
        if not isinstance(data, dict):
            raise MalformedResponseError(provider=self.provider)
        candidates = data.get("candidates")
        if not candidates:
            blocked = (data.get("promptFeedback") or {}).get("blockReason")
            if blocked:
                return LLMResponse(
                    finish_reason=FinishReason.CONTENT_FILTER,
                    provider=self.provider,
                    model=request.model,
                    usage=self._usage(data.get("usageMetadata")),
                )
            raise MalformedResponseError(provider=self.provider)
        candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        text, thoughts, calls = self._parts(candidate, 0)
        return LLMResponse(
            content="".join(text),
            reasoning="".join(thoughts) or None,
            tool_calls=calls,
            finish_reason=self._finish(candidate.get("finishReason"), bool(calls)),
            provider=self.provider,
            model=data.get("modelVersion")
            if isinstance(data.get("modelVersion"), str)
            else request.model,
            usage=self._usage(data.get("usageMetadata")),
            metadata={"response_id": data["responseId"]}
            if isinstance(data.get("responseId"), str)
            else {},
        )

    async def generate(self, conn: Connection, request: LLMRequest) -> LLMResponse:
        self.require_credential(conn)
        data = await request_json(
            conn,
            "POST",
            f"{_model_path(request.model)}:generateContent",
            headers=self.headers(conn),
            json_body=self.build_payload(request),
        )
        return self.parse_response(data, request)

    async def stream(self, conn: Connection, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        self.require_credential(conn)
        lines = stream_lines(
            conn,
            "POST",
            f"{_model_path(request.model)}:streamGenerateContent",
            headers=self.headers(conn),
            params={"alt": "sse"},
            json_body=self.build_payload(request),
        )
        usage: Usage | None = None
        finish: FinishReason | None = None
        call_count = 0
        async for _event, data in iter_sse(lines):
            chunk = loads_event(data, self.provider)
            if not isinstance(chunk, dict):
                raise MalformedResponseError(provider=self.provider)
            if "error" in chunk:
                raise from_stream_payload(chunk, provider=self.provider)
            if chunk.get("usageMetadata"):
                usage = self._usage(chunk["usageMetadata"])
            candidates = chunk.get("candidates") or []
            if not candidates and (chunk.get("promptFeedback") or {}).get("blockReason"):
                finish = FinishReason.CONTENT_FILTER
                continue
            for candidate in candidates[:1]:
                if not isinstance(candidate, dict):
                    continue
                text, thoughts, calls = self._parts(candidate, call_count)
                for piece in thoughts:
                    yield StreamEvent(type=StreamEventType.REASONING, text=piece)
                for piece in text:
                    if piece:
                        yield StreamEvent(type=StreamEventType.TEXT, text=piece)
                for call in calls:
                    yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=call)
                call_count += len(calls)
                if candidate.get("finishReason"):
                    finish = self._finish(candidate["finishReason"], call_count > 0)
        if usage is not None:
            yield StreamEvent(type=StreamEventType.USAGE, usage=usage)
        yield StreamEvent(type=StreamEventType.FINISH, finish_reason=finish or FinishReason.OTHER)

    def model_info(self, item: dict[str, Any]) -> ModelInfo:
        methods = item.get("supportedGenerationMethods")
        methods = methods if isinstance(methods, list) else None
        return ModelInfo(
            provider=self.provider,
            id=item["name"].removeprefix("models/"),
            display_name=item.get("displayName")
            if isinstance(item.get("displayName"), str)
            else None,
            context_window=as_positive(item.get("inputTokenLimit")),
            max_output_tokens=as_positive(item.get("outputTokenLimit")),
            capabilities=LLMCapabilities(
                chat="generateContent" in methods if methods is not None else None,
                embeddings="embedContent" in methods if methods is not None else None,
                reasoning=as_bool(item.get("thinking")),
            ),
        )

    async def list_models(self, conn: Connection) -> list[ModelInfo]:
        self.require_credential(conn)
        models: list[ModelInfo] = []
        params: dict[str, Any] = {"pageSize": 1000}
        for _ in range(MAX_MODEL_PAGES):
            data = await request_json(
                conn, "GET", "/models", headers=self.headers(conn), params=params
            )
            items = data.get("models") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise MalformedResponseError(provider=self.provider)
            models += [
                self.model_info(i)
                for i in items
                if isinstance(i, dict) and isinstance(i.get("name"), str) and i["name"]
            ]
            token = data.get("nextPageToken")
            if not isinstance(token, str) or not token:
                break
            params = {"pageSize": 1000, "pageToken": token}
        return models
