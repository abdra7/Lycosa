"""Provider-neutral request/response contract.

Adapters translate to and from these types. Capability flags are tri-state:
True (documented/discovered support), False (documented absence) and None
(unknown). Unknown is never reported as supported.
"""

import enum
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TEXT_CHARS = 400_000
# base64 of an ~8 MB image; images are inline only (no fetch-by-URL path)
MAX_IMAGE_B64_CHARS = 11_200_000
MAX_IMAGES_PER_REQUEST = 8
TOOL_NAME_PATTERN = r"^[a-zA-Z0-9_-]{1,64}$"


class Role(enum.StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TextPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"] = "text"
    text: str = Field(max_length=MAX_TEXT_CHARS)


class ImagePart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["image"] = "image"
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    data: str = Field(min_length=1, max_length=MAX_IMAGE_B64_CHARS)  # base64, no data: prefix


ContentPart = Annotated[TextPart | ImagePart, Field(discriminator="type")]


class ToolCall(BaseModel):
    """A complete tool invocation requested by the model. Lycosa does not run
    tools itself; callers receive the call and decide what to do with it."""

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    # opaque provider fields that must be echoed back on the next turn
    # (e.g. Gemini thought signatures); never interpreted by Lycosa
    extra: dict[str, str] = Field(default_factory=dict, max_length=4)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role
    content: (
        Annotated[str, Field(max_length=MAX_TEXT_CHARS)]
        | Annotated[list[ContentPart], Field(max_length=64)]
    ) = ""
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=128)  # assistant only
    tool_call_id: str | None = Field(default=None, max_length=200)  # tool only
    name: str | None = Field(default=None, pattern=TOOL_NAME_PATTERN)  # tool name, tool only

    @model_validator(mode="after")
    def _role_rules(self) -> "Message":
        if self.tool_calls and self.role != Role.ASSISTANT:
            raise ValueError("only assistant messages carry tool_calls")
        if self.role == Role.TOOL and not self.tool_call_id:
            raise ValueError("tool messages need tool_call_id")
        if self.role != Role.TOOL and (self.tool_call_id or self.name):
            raise ValueError("tool_call_id/name are only valid on tool messages")
        return self

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "".join(p.text for p in self.content if isinstance(p, TextPart))

    def images(self) -> list[ImagePart]:
        if isinstance(self.content, str):
            return []
        return [p for p in self.content if isinstance(p, ImagePart)]


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str = Field(default="", max_length=4000)
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "none", "required", "tool"] = "auto"
    name: str | None = Field(default=None, pattern=TOOL_NAME_PATTERN)

    @model_validator(mode="after")
    def _named(self) -> "ToolChoice":
        if (self.mode == "tool") != (self.name is not None):
            raise ValueError("tool_choice.name is required exactly when mode is 'tool'")
        return self


class ResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "json_object", "json_schema"] = "text"
    name: str = Field(default="response", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    json_schema: dict[str, Any] | None = None
    strict: bool = True

    @model_validator(mode="after")
    def _schema(self) -> "ResponseFormat":
        if (self.type == "json_schema") != (self.json_schema is not None):
            raise ValueError("json_schema is required exactly when type is 'json_schema'")
        return self


class LLMRequest(BaseModel):
    """Provider-independent chat request. Sampling parameters left as None are
    not sent, so each provider applies its own default (some newer models
    reject non-default temperature values outright)."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)  # provider-native id, no prefix
    messages: list[Message] = Field(min_length=1, max_length=500)
    system: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)
    stop: list[str] = Field(default_factory=list, max_length=4)
    tools: list[ToolSpec] = Field(default_factory=list, max_length=128)
    tool_choice: ToolChoice | None = None
    parallel_tool_calls: bool | None = None
    response_format: ResponseFormat | None = None
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    # Lycosa-side correlation only (task/agent ids); never sent to a provider
    metadata: dict[str, str] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def _bounds(self) -> "LLMRequest":
        if sum(len(m.images()) for m in self.messages) > MAX_IMAGES_PER_REQUEST:
            raise ValueError(f"at most {MAX_IMAGES_PER_REQUEST} images per request")
        if self.tool_choice is not None and self.tool_choice.mode != "none" and not self.tools:
            raise ValueError("tool_choice requires tools")
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        return self

    def uses_vision(self) -> bool:
        return any(m.images() for m in self.messages)

    def system_text(self) -> str:
        """Top-level system prompt plus any system-role messages, in order."""
        parts = [self.system] if self.system else []
        parts += [m.text() for m in self.messages if m.role == Role.SYSTEM]
        return "\n\n".join(p for p in parts if p)


class FinishReason(enum.StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    OTHER = "other"


class Usage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    # cost reported by the provider itself (e.g. OpenRouter), never estimated here
    reported_cost: float | None = None

    def with_total(self) -> "Usage":
        if self.total_tokens is None and None not in (self.prompt_tokens, self.completion_tokens):
            return self.model_copy(
                update={"total_tokens": (self.prompt_tokens or 0) + (self.completion_tokens or 0)}
            )
        return self


class LLMResponse(BaseModel):
    content: str = ""
    reasoning: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: FinishReason
    provider: str
    model: str
    usage: Usage = Field(default_factory=Usage)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StreamEventType(enum.StrEnum):
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL_DELTA = "tool_call_delta"  # partial arguments as they arrive
    TOOL_CALL = "tool_call"  # a complete, parsed call
    USAGE = "usage"
    FINISH = "finish"


class StreamEvent(BaseModel):
    type: StreamEventType
    text: str | None = None
    index: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str | None = None
    tool_call: ToolCall | None = None
    usage: Usage | None = None
    finish_reason: FinishReason | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMCapabilities(BaseModel):
    """True = supported, False = not supported, None = unknown/varies by model."""

    chat: bool | None = None
    streaming: bool | None = None
    tool_calling: bool | None = None
    parallel_tools: bool | None = None
    structured_output: bool | None = None  # JSON-schema constrained output
    json_mode: bool | None = None  # free-form JSON object mode
    vision: bool | None = None
    reasoning: bool | None = None
    embeddings: bool | None = None

    def merged_with(self, model: "LLMCapabilities | None") -> "LLMCapabilities":
        """Model-level discovery overrides the provider-level declaration; a
        provider-level False (the API itself lacks the feature) always wins."""
        if model is None:
            return self.model_copy()
        values = {}
        for name in type(self).model_fields:
            api, discovered = getattr(self, name), getattr(model, name)
            values[name] = (
                False if api is False else (discovered if discovered is not None else api)
            )
        return LLMCapabilities(**values)


class ModelInfo(BaseModel):
    provider: str
    id: str
    display_name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    capabilities: LLMCapabilities = Field(default_factory=LLMCapabilities)

    @property
    def ref(self) -> str:
        return f"{self.provider}:{self.id}"


class HealthStatus(BaseModel):
    status: Literal["ready", "offline", "unauthorized", "error", "unknown"]
    detail: str = ""
    latency_ms: int | None = None
    models_available: int | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
