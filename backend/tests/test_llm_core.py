"""Universal LLM layer: normalized types, model refs, capabilities, errors, registry."""

import pytest
from pydantic import ValidationError

from app.llm import catalog, errors
from app.llm.adapters.base import ToolCallAccumulator, parse_arguments
from app.llm.modelref import parse_model_ref
from app.llm.spec import AuthMethod, ProviderKind
from app.llm.types import (
    ImagePart,
    LLMCapabilities,
    LLMRequest,
    Message,
    ResponseFormat,
    TextPart,
    ToolChoice,
    ToolSpec,
)

REQUIRED_PROVIDERS = {
    "openai",
    "anthropic",
    "gemini",
    "deepseek",
    "qwen",
    "xai",
    "mistral",
    "openrouter",
    "ollama",
    "lmstudio",
    "vllm",
}


def user(text="hi"):
    return Message(role="user", content=text)


# --- registry ------------------------------------------------------------------


def test_every_required_provider_is_registered_with_an_adapter():
    ids = {s.id for s in catalog.specs()}
    assert REQUIRED_PROVIDERS <= ids
    for provider in REQUIRED_PROVIDERS:
        adapter = catalog.get(provider)
        assert adapter.provider == provider
        assert adapter.spec.capabilities.chat is True
        assert adapter.spec.capabilities.streaming is True


def test_local_providers_need_no_credential_and_cloud_ones_do():
    for provider in ("ollama", "lmstudio", "vllm"):
        spec = catalog.spec(provider)
        assert spec.kind == ProviderKind.LOCAL
        assert not spec.credential_required and spec.allow_http
    for provider in REQUIRED_PROVIDERS - {"ollama", "lmstudio", "vllm"}:
        spec = catalog.spec(provider)
        assert spec.credential_required and not spec.allow_http
        assert spec.default_base_url.startswith("https://")


def test_oauth_is_declared_only_where_officially_available():
    with_oauth = {s.id for s in catalog.specs() if AuthMethod.OAUTH_PKCE in s.auth_methods}
    assert with_oauth == {"openrouter"}


def test_consumer_subscriptions_are_not_presented_as_api_access():
    for provider in ("openai", "anthropic", "gemini"):
        note = catalog.spec(provider).subscription_note or ""
        assert "API" in note and "subscription" in note


def test_no_adapter_claims_embeddings():
    assert all(s.capabilities.embeddings is False for s in catalog.specs())


def test_unknown_provider_lookup_fails():
    assert not catalog.known("nope")
    with pytest.raises(KeyError):
        catalog.get("nope")


def test_qwen_accepts_only_official_regional_endpoints():
    spec = catalog.spec("qwen")
    assert spec.accepts_official_base_url("https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    assert spec.accepts_official_base_url(
        "https://ws123.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert not spec.accepts_official_base_url("https://evil.example/compatible-mode/v1")
    assert not spec.accepts_official_base_url(
        "https://dashscope-intl.aliyuncs.com.evil.example/compatible-mode/v1"
    )


# --- model references ------------------------------------------------------------


@pytest.mark.parametrize(
    "value,provider,model",
    [
        ("openai:gpt-x", "openai", "gpt-x"),
        ("ollama:llama3.2:1b", "ollama", "llama3.2:1b"),
        ("openrouter:vendor/model:free", "openrouter", "vendor/model:free"),
    ],
)
def test_model_ref_splits_on_first_colon(value, provider, model):
    ref = parse_model_ref(value)
    assert (ref.provider, ref.model) == (provider, model)
    assert str(ref) == value


@pytest.mark.parametrize(
    "value", ["gpt-x", "OpenAI:x", ":x", "openai:", "openai:a b", "x:http://a"]
)
def test_model_ref_rejects_malformed(value):
    with pytest.raises(ValueError):
        parse_model_ref(value)


# --- request validation ------------------------------------------------------------


def test_request_defaults_leave_sampling_to_the_provider():
    request = LLMRequest(model="m", messages=[user()])
    assert request.temperature is None and request.max_tokens is None


def test_request_rejects_unknown_fields_and_bad_shapes():
    with pytest.raises(ValidationError):
        LLMRequest(model="m", messages=[user()], api_key="x")
    with pytest.raises(ValidationError):
        LLMRequest(model="m", messages=[])
    with pytest.raises(ValidationError):  # tool_choice without tools
        LLMRequest(model="m", messages=[user()], tool_choice=ToolChoice(mode="required"))
    with pytest.raises(ValidationError):  # duplicate tool names
        LLMRequest(
            model="m",
            messages=[user()],
            tools=[ToolSpec(name="a"), ToolSpec(name="a")],
        )
    with pytest.raises(ValidationError):
        ToolChoice(mode="tool")
    with pytest.raises(ValidationError):
        ResponseFormat(type="json_schema")


def test_message_role_rules():
    with pytest.raises(ValidationError):
        Message(role="tool", content="x")  # missing tool_call_id
    with pytest.raises(ValidationError):
        Message(role="user", content="x", tool_call_id="c1")
    with pytest.raises(ValidationError):
        Message(role="user", tool_calls=[{"id": "c", "name": "f"}])


def test_image_limit_and_helpers():
    image = ImagePart(media_type="image/png", data="aGk=")
    message = Message(role="user", content=[TextPart(text="look"), image])
    assert message.text() == "look" and message.images() == [image]
    request = LLMRequest(model="m", messages=[message], system="sys")
    assert request.uses_vision()
    with pytest.raises(ValidationError):
        LLMRequest(model="m", messages=[Message(role="user", content=[image] * 9)])


def test_system_text_merges_top_level_and_system_messages():
    request = LLMRequest(
        model="m",
        system="one",
        messages=[Message(role="system", content="two"), user()],
    )
    assert request.system_text() == "one\n\ntwo"


# --- capabilities ------------------------------------------------------------------


def test_capability_merge_is_conservative():
    api = LLMCapabilities(tool_calling=True, vision=None, json_mode=False)
    model = LLMCapabilities(tool_calling=False, vision=True, json_mode=True)
    merged = api.merged_with(model)
    assert merged.tool_calling is False  # discovery says no
    assert merged.vision is True  # discovery fills an unknown
    assert merged.json_mode is False  # the API itself lacks it
    assert api.merged_with(LLMCapabilities()).tool_calling is True
    assert LLMCapabilities().merged_with(None).vision is None  # unknown stays unknown


def test_check_request_refuses_known_unsupported_features():
    adapter = catalog.get("anthropic")
    request = LLMRequest(
        model="m", messages=[user()], response_format=ResponseFormat(type="json_object")
    )
    with pytest.raises(errors.UnsupportedCapabilityError):
        adapter.check_request(request)
    vision = LLMRequest(
        model="m",
        messages=[Message(role="user", content=[ImagePart(media_type="image/png", data="aGk=")])],
    )
    from app.llm.types import ModelInfo

    blind = ModelInfo(provider="anthropic", id="m", capabilities=LLMCapabilities(vision=False))
    with pytest.raises(errors.UnsupportedCapabilityError):
        adapter.check_request(vision, blind)
    adapter.check_request(vision)  # unknown support is attempted, not refused
    effort = LLMRequest(model="m", messages=[user()], reasoning_effort="high")
    with pytest.raises(errors.UnsupportedCapabilityError):
        catalog.get("qwen").check_request(effort)  # adapter has no effort parameter


# --- error normalization -------------------------------------------------------------


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, {"error": {"message": "bad key"}}, errors.AuthenticationError),
        (403, {"error": {"type": "permission_error"}}, errors.PermissionDeniedError),
        (402, {"error": {"message": "Insufficient Balance"}}, errors.QuotaExceededError),
        (429, {"error": {"code": "insufficient_quota"}}, errors.QuotaExceededError),
        (429, {"error": {"type": "rate_limit_error"}}, errors.RateLimitError),
        (404, {"error": "model 'x' not found"}, errors.ModelNotFoundError),
        (400, {"error": {"code": "model_not_found"}}, errors.ModelNotFoundError),
        (400, {"error": {"code": "context_length_exceeded"}}, errors.ContextLengthError),
        (
            400,
            {"error": {"message": "prompt is too long: 300000 tokens"}},
            errors.ContextLengthError,
        ),
        (400, {"error": {"message": "bad param"}}, errors.InvalidRequestError),
        (500, None, errors.ProviderUnavailableError),
        (
            529,
            {"type": "error", "error": {"type": "overloaded_error"}},
            errors.ProviderUnavailableError,
        ),
        (408, None, errors.LLMTimeoutError),
    ],
)
def test_http_errors_are_classified(status, body, expected):
    error = errors.from_http(status, body, provider="p")
    assert type(error) is expected
    assert error.provider == "p" and error.status == status


def test_classification_never_echoes_provider_text():
    secret = "sk-live-SECRET prompt text"
    error = errors.from_http(400, {"error": {"message": secret}}, provider="p")
    assert secret not in str(error) and secret not in error.public_message


def test_retryability_flags():
    assert errors.RateLimitError.retryable and errors.ProviderUnavailableError.retryable
    assert errors.LLMTimeoutError.retryable
    for permanent in (
        errors.AuthenticationError,
        errors.QuotaExceededError,
        errors.InvalidRequestError,
        errors.ModelNotFoundError,
        errors.ContextLengthError,
    ):
        assert not permanent.retryable


def test_upstream_errors_never_map_to_caller_auth_statuses():
    for cls in (errors.AuthenticationError, errors.PermissionDeniedError):
        assert cls.http_status not in (401, 403)


@pytest.mark.parametrize(
    "header,expected", [("2", 2.0), ("0", 0.0), ("9999", 60.0), ("soon", None), (None, None)]
)
def test_retry_after_parsing(header, expected):
    assert errors.parse_retry_after(header) == expected


def test_retry_after_header_reaches_the_error():
    error = errors.from_http(429, {}, provider="p", headers={"retry-after": "3"})
    assert isinstance(error, errors.RateLimitError) and error.retry_after == 3.0


# --- tool-call helpers -----------------------------------------------------------------


def test_tool_arguments_must_be_a_json_object():
    assert parse_arguments('{"a": 1}', "p") == {"a": 1}
    assert parse_arguments("", "p") == {} and parse_arguments(None, "p") == {}
    for bad in ("{not json", "[1, 2]", 7):
        with pytest.raises(errors.ToolCallingError):
            parse_arguments(bad, "p")


def test_tool_call_accumulator_assembles_fragments_by_index():
    acc = ToolCallAccumulator("p")
    acc.add(0, "call_a", "get_weather", '{"ci')
    acc.add(1, "call_b", "lookup", "{}")
    acc.add(0, None, None, 'ty": "Oslo"}')
    calls = acc.complete()
    assert [(c.id, c.name, c.arguments) for c in calls] == [
        ("call_a", "get_weather", {"city": "Oslo"}),
        ("call_b", "lookup", {}),
    ]
    broken = ToolCallAccumulator("p")
    broken.add(0, "c", "f", '{"x":')
    with pytest.raises(errors.ToolCallingError):
        broken.complete()
