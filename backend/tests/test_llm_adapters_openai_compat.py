"""OpenAI-wire-format adapters: OpenAI, DeepSeek, Qwen, xAI, Mistral, OpenRouter,
LM Studio, vLLM and custom compatible endpoints. HTTP is intercepted with respx;
no live provider is contacted."""

import json

import httpx
import pytest
import respx

from app.llm import catalog, errors
from app.llm.adapters.base import collect
from app.llm.http import Connection
from app.llm.netpolicy import PUBLIC_ONLY, local_policy, parse_networks
from app.llm.types import (
    FinishReason,
    ImagePart,
    LLMRequest,
    Message,
    ResponseFormat,
    StreamEventType,
    TextPart,
    ToolCall,
    ToolChoice,
    ToolSpec,
)

KEY = "sk-test-not-real"
LAN = local_policy(parse_networks("127.0.0.0/8"))

# provider -> (token-limit field, sends stream usage option)
COMPAT = {
    "openai": ("max_completion_tokens", True),
    "deepseek": ("max_tokens", True),
    "qwen": ("max_tokens", True),
    "xai": ("max_completion_tokens", True),
    "mistral": ("max_tokens", False),
    "openrouter": ("max_tokens", True),
    "lmstudio": ("max_tokens", False),
    "vllm": ("max_tokens", True),
}


def conn_for(provider: str, credential: str | None = KEY) -> Connection:
    spec = catalog.spec(provider)
    return Connection(
        provider=provider,
        base_url=spec.default_base_url,
        credential=credential,
        policy=LAN if spec.allow_http else PUBLIC_ONLY,
    )


def completion(content="hello", **extra):
    message = {"role": "assistant", "content": content, **extra.pop("message", {})}
    return {
        "id": "resp-1",
        "model": "served-model",
        "choices": [{"index": 0, "message": message, "finish_reason": extra.pop("finish", "stop")}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, **extra},
    }


def sse(*events) -> bytes:
    body = "".join(f"data: {json.dumps(e) if not isinstance(e, str) else e}\n\n" for e in events)
    return body.encode()


TOOL = ToolSpec(
    name="get_weather",
    description="Weather by city",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


@pytest.mark.parametrize("provider", sorted(COMPAT))
@respx.mock
async def test_generate_translation_and_normalized_response(provider):
    spec = catalog.spec(provider)
    route = respx.post(f"{spec.default_base_url}/chat/completions").mock(
        return_value=httpx.Response(200, json=completion())
    )
    request = LLMRequest(
        model="some-model",
        system="be brief",
        messages=[Message(role="user", content="hi")],
        max_tokens=50,
        temperature=0.3,
        stop=["END"],
    )
    response = await catalog.get(provider).generate(conn_for(provider), request)

    sent = json.loads(route.calls.last.request.content)
    token_field, _ = COMPAT[provider]
    assert sent[token_field] == 50
    other = {"max_tokens", "max_completion_tokens"} - {token_field}
    assert not other & sent.keys()
    assert sent["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    assert sent["temperature"] == 0.3 and sent["stop"] == ["END"] and sent["stream"] is False
    assert route.calls.last.request.headers["authorization"] == f"Bearer {KEY}"

    assert response.content == "hello"
    assert response.provider == provider and response.model == "served-model"
    assert response.finish_reason == FinishReason.STOP
    assert response.usage.prompt_tokens == 5 and response.usage.total_tokens == 7
    assert response.metadata["response_id"] == "resp-1"


@respx.mock
async def test_unset_sampling_parameters_are_not_sent():
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion())
    )
    await catalog.get("openai").generate(
        conn_for("openai"), LLMRequest(model="m", messages=[Message(role="user", content="x")])
    )
    sent = json.loads(route.calls.last.request.content)
    assert "temperature" not in sent and "max_completion_tokens" not in sent


@respx.mock
async def test_tools_images_and_structured_output_translation():
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=completion(
                content=None,
                finish="tool_calls",
                message={
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"city":"Oslo"}'},
                        }
                    ]
                },
            ),
        )
    )
    request = LLMRequest(
        model="m",
        messages=[
            Message(
                role="user",
                content=[
                    TextPart(text="what is this"),
                    ImagePart(media_type="image/png", data="aGk="),
                ],
            ),
            Message(
                role="assistant",
                tool_calls=[ToolCall(id="call_0", name="get_weather", arguments={"city": "Rome"})],
            ),
            Message(role="tool", tool_call_id="call_0", content="sunny"),
        ],
        tools=[TOOL],
        tool_choice=ToolChoice(mode="tool", name="get_weather"),
        parallel_tool_calls=False,
        response_format=ResponseFormat(
            type="json_schema", name="w", json_schema={"type": "object"}
        ),
        reasoning_effort="low",
    )
    response = await catalog.get("openai").generate(conn_for("openai"), request)
    sent = json.loads(route.calls.last.request.content)

    assert sent["messages"][0]["content"] == [
        {"type": "text", "text": "what is this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGk="}},
    ]
    assert sent["messages"][1]["tool_calls"][0]["function"] == {
        "name": "get_weather",
        "arguments": '{"city": "Rome"}',
    }
    assert sent["messages"][2] == {"role": "tool", "tool_call_id": "call_0", "content": "sunny"}
    assert sent["tools"][0]["function"]["name"] == "get_weather"
    assert sent["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}}
    assert sent["parallel_tool_calls"] is False
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "w", "schema": {"type": "object"}, "strict": True},
    }
    assert sent["reasoning_effort"] == "low"

    assert response.finish_reason == FinishReason.TOOL_CALLS
    assert response.tool_calls == [
        ToolCall(id="call_1", name="get_weather", arguments={"city": "Oslo"})
    ]
    assert response.content == ""


@respx.mock
async def test_malformed_tool_arguments_are_rejected():
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=completion(
                content=None,
                finish="tool_calls",
                message={
                    "tool_calls": [{"id": "c", "function": {"name": "f", "arguments": "{oops"}}]
                },
            ),
        )
    )
    with pytest.raises(errors.ToolCallingError):
        await catalog.get("openai").generate(
            conn_for("openai"), LLMRequest(model="m", messages=[Message(role="user", content="x")])
        )


@respx.mock
async def test_deepseek_reasoning_content_is_normalized():
    respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(
            200, json=completion(message={"reasoning_content": "thinking..."})
        )
    )
    response = await catalog.get("deepseek").generate(
        conn_for("deepseek"), LLMRequest(model="m", messages=[Message(role="user", content="x")])
    )
    assert response.reasoning == "thinking..." and response.content == "hello"


@respx.mock
async def test_openrouter_reports_provider_cost_not_an_estimate():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion(cost=0.00042))
    )
    response = await catalog.get("openrouter").generate(
        conn_for("openrouter"), LLMRequest(model="m", messages=[Message(role="user", content="x")])
    )
    assert response.usage.reported_cost == 0.00042


@pytest.mark.parametrize("provider", sorted(COMPAT))
@respx.mock
async def test_streaming_text_tool_deltas_usage_and_finish(provider):
    spec = catalog.spec(provider)
    route = respx.post(f"{spec.default_base_url}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hel"}}]},
                {"choices": [{"index": 0, "delta": {"content": "lo"}}]},
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_9",
                                        "type": "function",
                                        "function": {"name": "get_weather", "arguments": '{"ci'},
                                    }
                                ]
                            },
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": 'ty":"Oslo"}'}}
                                ]
                            },
                        }
                    ]
                },
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 6}},
                "[DONE]",
            ),
        )
    )
    request = LLMRequest(model="m", messages=[Message(role="user", content="x")], tools=[TOOL])
    events = [e async for e in catalog.get(provider).stream(conn_for(provider), request)]
    types = [e.type for e in events]

    assert types[:2] == [StreamEventType.TEXT, StreamEventType.TEXT]
    assert types.count(StreamEventType.TOOL_CALL_DELTA) == 2
    assert types[-3:] == [StreamEventType.TOOL_CALL, StreamEventType.USAGE, StreamEventType.FINISH]
    assert events[-3].tool_call == ToolCall(
        id="call_9", name="get_weather", arguments={"city": "Oslo"}
    )
    assert events[-2].usage.total_tokens == 10
    assert events[-1].finish_reason == FinishReason.TOOL_CALLS

    sent = json.loads(route.calls.last.request.content)
    assert sent["stream"] is True
    assert ("stream_options" in sent) is COMPAT[provider][1]


@respx.mock
async def test_stream_collects_into_a_response_and_surfaces_stream_errors():
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(
                200,
                content=sse(
                    {"choices": [{"index": 0, "delta": {"content": "a"}}]},
                    {"choices": [{"index": 0, "delta": {"content": "b"}, "finish_reason": "stop"}]},
                    "[DONE]",
                ),
            ),
            httpx.Response(
                200, content=sse({"error": {"message": "overloaded", "type": "server"}})
            ),
        ]
    )
    request = LLMRequest(model="m", messages=[Message(role="user", content="x")])
    adapter = catalog.get("openai")
    response = await collect(
        adapter.stream(conn_for("openai"), request), provider="openai", model="m"
    )
    assert response.content == "ab" and response.finish_reason == FinishReason.STOP
    with pytest.raises(errors.ProviderUnavailableError):
        async for _ in adapter.stream(conn_for("openai"), request):
            pass


@pytest.mark.parametrize(
    "status,headers,expected",
    [
        (401, {}, errors.AuthenticationError),
        (429, {"retry-after": "7"}, errors.RateLimitError),
        (404, {}, errors.ModelNotFoundError),
        (503, {}, errors.ProviderUnavailableError),
    ],
)
@respx.mock
async def test_http_errors_are_normalized(status, headers, expected):
    respx.post("https://api.x.ai/v1/chat/completions").mock(
        return_value=httpx.Response(status, headers=headers, json={"error": {"message": "x"}})
    )
    with pytest.raises(expected) as excinfo:
        await catalog.get("xai").generate(
            conn_for("xai"), LLMRequest(model="m", messages=[Message(role="user", content="x")])
        )
    if status == 429:
        assert excinfo.value.retry_after == 7.0


async def test_cloud_adapter_refuses_to_call_without_a_credential():
    with pytest.raises(errors.CredentialUnavailableError):
        await catalog.get("openai").generate(
            conn_for("openai", credential=None),
            LLMRequest(model="m", messages=[Message(role="user", content="x")]),
        )


@pytest.mark.parametrize("provider", ["lmstudio", "vllm"])
@respx.mock
async def test_local_servers_work_without_a_key(provider):
    spec = catalog.spec(provider)
    route = respx.post(f"{spec.default_base_url}/chat/completions").mock(
        return_value=httpx.Response(200, json=completion())
    )
    await catalog.get(provider).generate(
        conn_for(provider, credential=None),
        LLMRequest(model="m", messages=[Message(role="user", content="x")]),
    )
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
async def test_model_discovery_uses_real_metadata_only():
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "vendor/a",
                        "name": "A",
                        "context_length": 128000,
                        "architecture": {"input_modalities": ["text", "image"]},
                        "supported_parameters": ["tools", "response_format", "reasoning"],
                        "top_provider": {"max_completion_tokens": 4096},
                    },
                    {"id": "vendor/b", "supported_parameters": ["temperature"]},
                ]
            },
        )
    )
    respx.get("https://api.mistral.ai/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "mistral-x",
                        "max_context_length": 32000,
                        "capabilities": {
                            "completion_chat": True,
                            "function_calling": False,
                            "vision": True,
                        },
                    }
                ]
            },
        )
    )
    respx.get("http://localhost:8000/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "served", "max_model_len": 8192}]})
    )
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-x", "owned_by": "openai"}]})
    )

    a, b = await catalog.get("openrouter").list_models(conn_for("openrouter"))
    assert (a.ref, a.display_name, a.context_window, a.max_output_tokens) == (
        "openrouter:vendor/a",
        "A",
        128000,
        4096,
    )
    assert a.capabilities.tool_calling and a.capabilities.vision and a.capabilities.reasoning
    assert a.capabilities.json_mode is True and a.capabilities.structured_output is False
    assert b.capabilities.tool_calling is False and b.capabilities.vision is None

    (m,) = await catalog.get("mistral").list_models(conn_for("mistral"))
    assert m.context_window == 32000
    assert (m.capabilities.chat, m.capabilities.tool_calling, m.capabilities.vision) == (
        True,
        False,
        True,
    )

    (v,) = await catalog.get("vllm").list_models(conn_for("vllm", credential=None))
    assert v.context_window == 8192 and v.capabilities.tool_calling is None

    (o,) = await catalog.get("openai").list_models(conn_for("openai"))
    assert o.context_window is None and o.capabilities.vision is None  # nothing invented


@respx.mock
async def test_health_check_is_a_real_authenticated_call():
    respx.get("https://api.deepseek.com/models").mock(
        side_effect=[
            httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]}),
            httpx.Response(401, json={}),
            httpx.ConnectError("down"),
            httpx.Response(404, json={}),
        ]
    )
    adapter = catalog.get("deepseek")
    ready = await adapter.health_check(conn_for("deepseek"))
    assert ready.status == "ready" and ready.models_available == 2
    assert (await adapter.health_check(conn_for("deepseek"))).status == "unauthorized"
    assert (await adapter.health_check(conn_for("deepseek"))).status == "offline"
    assert (await adapter.health_check(conn_for("deepseek"))).status == "unknown"
