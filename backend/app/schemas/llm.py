"""API shapes for /api/v1/llm. Credentials are write-only: no response model
has a field that could carry one."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.llm.types import (
    FinishReason,
    LLMCapabilities,
    Message,
    ResponseFormat,
    ToolCall,
    ToolChoice,
    ToolSpec,
    Usage,
)

PURPOSE_PATTERN = r"^(default|coding|reasoning|vision|cheap|private|offline)$"
LABEL_PATTERN = r"^[\w][\w .@()+-]{0,99}$"


class ProviderOut(BaseModel):
    id: str
    display_name: str
    kind: str
    auth_methods: list[str]
    credential_required: bool
    base_url_mode: str
    default_base_url: str
    official_base_urls: list[str]
    capabilities: LLMCapabilities
    docs_url: str
    subscription_note: str | None
    notes: list[str]


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=40)
    label: str = Field(pattern=LABEL_PATTERN)
    scope: Literal["personal", "deployment"] = "personal"
    base_url: str | None = Field(default=None, max_length=255)
    api_key: SecretStr | None = None


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, pattern=LABEL_PATTERN)
    status: Literal["active", "disabled"] | None = None
    base_url: str | None = Field(default=None, max_length=255)
    api_key: SecretStr | None = None


class AccountOut(BaseModel):
    id: uuid.UUID
    provider: str
    provider_name: str
    label: str
    scope: Literal["personal", "deployment"]
    owner_user_id: uuid.UUID | None
    mine: bool
    usable: bool
    manageable: bool
    auth_method: str
    base_url: str
    is_local: bool
    status: str
    api_access: Literal["available", "unavailable", "unknown"]
    credential_configured: bool
    last_tested_at: datetime | None
    last_test_status: str | None
    last_test_detail: str | None
    created_at: datetime
    updated_at: datetime


class AccountProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # optional: send a minimal prompt to this model (may incur a tiny charge)
    model: str | None = Field(default=None, min_length=1, max_length=200)


class AccountProbeOut(BaseModel):
    status: Literal["ready", "offline", "unauthorized", "error", "unknown"]
    detail: str
    latency_ms: int | None
    models_available: int | None
    api_access: Literal["available", "unavailable", "unknown"]
    subscription_note: str | None


class ModelOut(BaseModel):
    ref: str
    provider: str
    id: str
    account_id: uuid.UUID
    account_label: str
    display_name: str | None
    context_window: int | None
    max_output_tokens: int | None
    capabilities: LLMCapabilities


class ModelListOut(BaseModel):
    models: list[ModelOut]
    errors: list[dict[str, str]] = []  # per-account discovery failures (public messages)


class HealthOut(BaseModel):
    account_id: uuid.UUID
    provider: str
    status: Literal["ready", "offline", "unauthorized", "error", "unknown"]
    detail: str
    latency_ms: int | None
    models_available: int | None
    checked_at: datetime


class RouteEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    model: str = Field(min_length=1, max_length=200)


class RoutingPolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["personal", "deployment"] = "personal"
    chain: list[RouteEntry] = Field(min_length=1, max_length=5)


class RoutingPolicyOut(BaseModel):
    purpose: str
    scope: Literal["personal", "deployment"]
    chain: list[RouteEntry]
    updated_at: datetime


class LLMTarget(BaseModel):
    """Either an explicit account+model, or a routing purpose."""

    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    purpose: str | None = Field(default=None, pattern=PURPOSE_PATTERN)

    @model_validator(mode="after")
    def _one_target(self) -> "LLMTarget":
        explicit = self.account_id is not None or self.model is not None
        if explicit and self.purpose is not None:
            raise ValueError("give account_id+model or purpose, not both")
        if explicit and (self.account_id is None or self.model is None):
            raise ValueError("account_id and model go together")
        return self


class ChatRequest(LLMTarget):
    messages: list[Message] = Field(min_length=1, max_length=500)
    system: str | None = Field(default=None, max_length=400_000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)
    stop: list[str] = Field(default_factory=list, max_length=4)
    tools: list[ToolSpec] = Field(default_factory=list, max_length=128)
    tool_choice: ToolChoice | None = None
    parallel_tool_calls: bool | None = None
    response_format: ResponseFormat | None = None
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    stream: bool = False


class PromptCheckRequest(LLMTarget):
    prompt: str = Field(min_length=1, max_length=20_000)
    max_tokens: int = Field(default=256, ge=1, le=4096)


class ChatResponse(BaseModel):
    content: str
    reasoning: str | None
    tool_calls: list[ToolCall]
    finish_reason: FinishReason
    provider: str
    model: str
    account_id: uuid.UUID
    usage: Usage
    latency_ms: int
    attempts: int
    fallback_index: int
    estimated_cost: float | None
    cost_source: str | None


class UsageOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    created_at: datetime
    request_id: str
    user_id: uuid.UUID | None
    account_id: uuid.UUID | None
    provider: str
    model: str
    purpose: str | None
    task_id: uuid.UUID | None
    status: str
    error_code: str | None
    attempts: int
    fallback_index: int
    stream: bool
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    estimated_cost: float | None
    cost_source: str | None
    currency: str | None


class OAuthStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # loopback URL the desktop app listens on; omit for copy-the-code mode
    callback_url: str | None = Field(default=None, max_length=200)


class OAuthStartOut(BaseModel):
    authorization_url: str
    flow: str  # opaque, sealed, user-bound, short-lived
    expires_in: int


class OAuthCompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow: str = Field(min_length=1, max_length=2000)
    code: str = Field(min_length=1, max_length=2000)
    label: str = Field(default="OpenRouter", pattern=LABEL_PATTERN)
    scope: Literal["personal", "deployment"] = "personal"


def account_out(
    account: Any, *, mine: bool, usable: bool, manageable: bool, has_key: bool
) -> AccountOut:
    from app.llm import catalog

    return AccountOut(
        id=account.id,
        provider=account.provider,
        provider_name=catalog.spec(account.provider).display_name
        if catalog.known(account.provider)
        else account.provider,
        label=account.label,
        scope="deployment" if account.owner_user_id is None else "personal",
        owner_user_id=account.owner_user_id,
        mine=mine,
        usable=usable,
        manageable=manageable,
        auth_method=account.auth_method,
        base_url=account.base_url,
        is_local=account.is_local,
        status=account.status,
        api_access=account.api_access,
        credential_configured=has_key,
        last_tested_at=account.last_tested_at,
        last_test_status=account.last_test_status,
        last_test_detail=account.last_test_detail,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )
