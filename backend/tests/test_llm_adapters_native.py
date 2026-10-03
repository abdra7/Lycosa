"""Native-format adapters: Anthropic Messages, Gemini generateContent, Ollama
/api/chat. HTTP is intercepted with respx; no live provider is contacted."""

import json

import httpx
import pytest
import respx

from app.llm import catalog, errors
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
TOOL = ToolSpec(
    name="lookup", parameters={"type": "object", "properties": {"q": {"type": "string"}}}
)


def sse(*events: tuple[str, dict]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


def user(text="hi"):
    return Message(role="user", content=text)


# --- Anthropic -------------------------------------------------------------------

ANTHROPIC = Connection(
    provider="anthropic", base_url="https://api.anthropic.com/v1", credential=KEY
)


@respx.mock
async def test_anthropic_request_translation():
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_1",
                "model": "claude-x",
                "content": [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "Looking it up."},
                    {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"q": "x"}},
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 3},
            },
        )
    )
    request = LLMRequest(
        model="claude-x",
        system="sys",
        messages=[
            Message(role="system", content="more sys"),
            Message(
                role="user",
                content=[TextPart(text="see"), ImagePart(media_type="image/jpeg", data="aGk=")],
            ),
            Message(
                role="assistant",
                content="calling",
                tool_calls=[ToolCall(id="toolu_0", name="lookup", arguments={"q": "a"})],
            ),
            Message(role="tool", tool_call_id="toolu_0", content="result A"),
            Message(role="user", content="and now?"),
        ],
        tools=[TOOL],
        tool_choice=ToolChoice(mode="required"),
        parallel_tool_calls=False,
        response_format=ResponseFormat(type="json_schema", json_schema={"type": "object"}),
        reasoning_effort="medium",
    )
    response = await catalog.get("anthropic").generate(ANTHROPIC, request)
    call = route.calls.last.request
    sent = json.loads(call.content)

    assert call.headers["x-api-key"] == KEY and call.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in call.headers
    assert sent["system"] == "sys\n\nmore sys"
    assert sent["max_tokens"] == 4096  # required by the API; default applied
    assert "temperature" not in sent  # newer models reject non-default temperature
    assert sent["messages"][0]["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": "aGk="},
    }
    assert sent["messages"][1]["content"][1] == {
        "type": "tool_use",
        "id": "toolu_0",
        "name": "lookup",
        "input": {"q": "a"},
    }
    # tool result and the following user turn merge into one user message
    assert sent["messages"][2]["role"] == "user"
    assert [b["type"] for b in sent["messages"][2]["content"]] == ["tool_result", "text"]
    assert sent["tools"][0]["input_schema"] == TOOL.parameters
    assert sent["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}
    assert sent["output_config"] == {
        "format": {"type": "json_schema", "schema": {"type": "object"}},
        "effort": "medium",
    }

    assert response.content == "Looking it up." and response.reasoning == "hmm"
    assert response.tool_calls == [ToolCall(id="toolu_1", name="lookup", arguments={"q": "x"})]
    assert response.finish_reason == FinishReason.TOOL_CALLS
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (15, 3)
    assert response.usage.total_tokens == 18


@respx.mock
async def test_anthropic_streaming_events():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=sse(
                (
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {"usage": {"input_tokens": 9, "output_tokens": 1}},
                    },
                ),
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                ("ping", {"type": "ping"}),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "Hi"},
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu_2",
                            "name": "lookup",
                            "input": {},
                        },
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "input_json_delta", "partial_json": '{"q": '},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "input_json_delta", "partial_json": '"z"}'},
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 1}),
                (
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {"output_tokens": 12},
                    },
                ),
                ("message_stop", {"type": "message_stop"}),
            ),
        )
    )
    request = LLMRequest(model="claude-x", messages=[user()], tools=[TOOL])
    events = [e async for e in catalog.get("anthropic").stream(ANTHROPIC, request)]
    assert events[0].type == StreamEventType.TEXT and events[0].text == "Hi"
    calls = [e.tool_call for e in events if e.type == StreamEventType.TOOL_CALL]
    assert calls == [ToolCall(id="toolu_2", name="lookup", arguments={"q": "z"})]
    usage = next(e.usage for e in events if e.type == StreamEventType.USAGE)
    assert (usage.prompt_tokens, usage.completion_tokens) == (9, 12)
    assert events[-1].finish_reason == FinishReason.TOOL_CALLS


@respx.mock
async def test_anthropic_stream_error_event_and_overload_status():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        side_effect=[
            httpx.Response(
                200,
                content=sse(
                    (
                        "error",
                        {
                            "type": "error",
                            "error": {"type": "overloaded_error", "message": "Overloaded"},
                        },
                    )
                ),
            ),
            httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error"}}),
        ]
    )
    adapter = catalog.get("anthropic")
    request = LLMRequest(model="claude-x", messages=[user()])
    with pytest.raises(errors.ProviderUnavailableError):
        async for _ in adapter.stream(ANTHROPIC, request):
            pass
    with pytest.raises(errors.ProviderUnavailableError):
        await adapter.generate(ANTHROPIC, request)


@respx.mock
async def test_anthropic_model_discovery_reads_documented_capabilities_and_pages():
    route = respx.get("https://api.anthropic.com/v1/models").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "claude-a",
                            "display_name": "Claude A",
                            "max_input_tokens": 200000,
                            "max_tokens": 64000,
                            "capabilities": {
                                "image_input": {"supported": True},
                                "structured_outputs": {"supported": False},
                                "thinking": {"supported": True, "types": {}},
                            },
                        }
                    ],
                    "has_more": True,
                    "last_id": "claude-a",
                },
            ),
            httpx.Response(
                200,
                json={"data": [{"id": "claude-b", "max_input_tokens": 0}], "has_more": False},
            ),
        ]
    )
    a, b = await catalog.get("anthropic").list_models(ANTHROPIC)
    assert (a.display_name, a.context_window, a.max_output_tokens) == ("Claude A", 200000, 64000)
    assert a.capabilities.vision is True and a.capabilities.structured_output is False
    assert a.capabilities.reasoning is True and a.capabilities.tool_calling is None
    assert b.context_window is None  # a zero/placeholder limit is not reported as a fact
    assert route.calls[1].request.url.params["after_id"] == "claude-a"


def test_anthropic_refuses_free_form_json_mode():
    request = LLMRequest(
        model="m", messages=[user()], response_format=ResponseFormat(type="json_object")
    )
    with pytest.raises(errors.UnsupportedCapabilityError):
        catalog.get("anthropic").check_request(request)


# --- Gemini ---------------------------------------------------------------------

GEMINI = Connection(
    provider="gemini", base_url="https://generativelanguage.googleapis.com/v1beta", credential=KEY
)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-x"


@respx.mock
async def test_gemini_request_translation_and_key_in_header_not_url():
    route = respx.post(f"{GEMINI_URL}:generateContent").mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "plan", "thought": True},
                                {"text": "Answer"},
                                {
                                    "functionCall": {
                                        "id": "fc1",
                                        "name": "lookup",
                                        "args": {"q": "y"},
                                    },
                                    "thoughtSignature": "sig==",
                                },
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 8,
                    "candidatesTokenCount": 4,
                    "totalTokenCount": 15,
                    "thoughtsTokenCount": 3,
                },
                "modelVersion": "gemini-x-001",
                "responseId": "r1",
            },
        )
    )
    request = LLMRequest(
        model="models/gemini-x",
        system="sys",
        messages=[
            Message(
                role="user",
                content=[TextPart(text="pic"), ImagePart(media_type="image/webp", data="aGk=")],
            ),
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="fc0",
                        name="lookup",
                        arguments={"q": "a"},
                        extra={"thought_signature": "s0"},
                    )
                ],
            ),
            Message(role="tool", tool_call_id="fc0", content="A"),
        ],
        tools=[TOOL],
        tool_choice=ToolChoice(mode="tool", name="lookup"),
        temperature=0.5,
        max_tokens=100,
        response_format=ResponseFormat(type="json_schema", json_schema={"type": "object"}),
    )
    response = await catalog.get("gemini").generate(GEMINI, request)
    call = route.calls.last.request
    sent = json.loads(call.content)

    assert call.headers["x-goog-api-key"] == KEY
    assert KEY not in str(call.url)
    assert sent["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert sent["contents"][0]["parts"][1] == {
        "inlineData": {"mimeType": "image/webp", "data": "aGk="}
    }
    assert sent["contents"][1] == {
        "role": "model",
        "parts": [
            {
                "functionCall": {"name": "lookup", "args": {"q": "a"}, "id": "fc0"},
                "thoughtSignature": "s0",
            }
        ],
    }
    assert sent["contents"][2]["parts"][0]["functionResponse"] == {
        "name": "lookup",  # recovered from the matching call
        "response": {"result": "A"},
        "id": "fc0",
    }
    assert sent["generationConfig"] == {
        "temperature": 0.5,
        "maxOutputTokens": 100,
        "responseMimeType": "application/json",
        "responseJsonSchema": {"type": "object"},
    }
    assert sent["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == TOOL.parameters
    assert sent["toolConfig"] == {
        "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["lookup"]}
    }

    assert response.content == "Answer" and response.reasoning == "plan"
    assert response.tool_calls[0].extra == {"thought_signature": "sig=="}
    assert response.finish_reason == FinishReason.TOOL_CALLS
    assert response.usage.reasoning_tokens == 3 and response.model == "gemini-x-001"


@respx.mock
async def test_gemini_streaming_and_safety_block():
    respx.post(f"{GEMINI_URL}:streamGenerateContent").mock(
        return_value=httpx.Response(
            200,
            content=(
                b'data: {"candidates":[{"content":{"parts":[{"text":"He"}]}}]}\n\n'
                b'data: {"candidates":[{"content":{"parts":[{"text":"y"}]},"finishReason":"STOP"}],'
                b'"usageMetadata":{"promptTokenCount":2,"candidatesTokenCount":2}}\n\n'
            ),
        )
    )
    events = [
        e
        async for e in catalog.get("gemini").stream(
            GEMINI, LLMRequest(model="gemini-x", messages=[user()])
        )
    ]
    assert "".join(e.text for e in events if e.type == StreamEventType.TEXT) == "Hey"
    assert events[-2].usage.total_tokens == 4
    assert events[-1].finish_reason == FinishReason.STOP
    assert respx.calls.last.request.url.params["alt"] == "sse"

    respx.post(f"{GEMINI_URL}:generateContent").mock(
        return_value=httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    )
    blocked = await catalog.get("gemini").generate(
        GEMINI, LLMRequest(model="gemini-x", messages=[user()])
    )
    assert blocked.finish_reason == FinishReason.CONTENT_FILTER and blocked.content == ""


async def test_gemini_rejects_path_injection_in_model_names():
    with pytest.raises(errors.InvalidRequestError):
        await catalog.get("gemini").generate(
            GEMINI, LLMRequest(model="../../v1/files", messages=[user()])
        )


@respx.mock
async def test_gemini_model_discovery():
    respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-x",
                        "displayName": "Gemini X",
                        "inputTokenLimit": 1048576,
                        "outputTokenLimit": 65536,
                        "supportedGenerationMethods": ["generateContent", "countTokens"],
                        "thinking": True,
                    },
                    {"name": "models/embed-x", "supportedGenerationMethods": ["embedContent"]},
                ]
            },
        )
    )
    x, e = await catalog.get("gemini").list_models(GEMINI)
    assert (x.id, x.context_window, x.capabilities.chat, x.capabilities.reasoning) == (
        "gemini-x",
        1048576,
        True,
        True,
    )
    assert e.capabilities.chat is False and e.capabilities.embeddings is True


# --- Ollama ---------------------------------------------------------------------

OLLAMA = Connection(
    provider="ollama",
    base_url="http://localhost:11434",
    policy=local_policy(parse_networks("127.0.0.0/8")),
)


@respx.mock
async def test_ollama_chat_translation_without_a_credential():
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "llama3.2:1b",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "thinking": "think",
                    "tool_calls": [{"function": {"name": "lookup", "arguments": {"q": "o"}}}],
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 11,
                "eval_count": 5,
            },
        )
    )
    request = LLMRequest(
        model="llama3.2:1b",
        system="sys",
        messages=[
            Message(
                role="user",
                content=[TextPart(text="img"), ImagePart(media_type="image/png", data="aGk=")],
            ),
            Message(role="assistant", tool_calls=[ToolCall(id="c0", name="lookup", arguments={})]),
            Message(role="tool", tool_call_id="c0", content="r"),
        ],
        tools=[TOOL],
        temperature=0.1,
        max_tokens=64,
        response_format=ResponseFormat(type="json_object"),
    )
    response = await catalog.get("ollama").generate(OLLAMA, request)
    call = route.calls.last.request
    sent = json.loads(call.content)

    assert "authorization" not in call.headers
    assert sent["stream"] is False and sent["format"] == "json"
    assert sent["options"] == {"temperature": 0.1, "num_predict": 64}
    assert sent["messages"][1] == {"role": "user", "content": "img", "images": ["aGk="]}
    assert sent["messages"][2]["tool_calls"] == [{"function": {"name": "lookup", "arguments": {}}}]
    assert sent["messages"][3] == {"role": "tool", "content": "r", "tool_name": "lookup"}

    assert response.reasoning == "think"
    assert response.tool_calls == [ToolCall(id="call_0", name="lookup", arguments={"q": "o"})]
    assert response.finish_reason == FinishReason.TOOL_CALLS
    assert response.usage.total_tokens == 16


@respx.mock
async def test_ollama_ndjson_streaming_and_missing_model():
    respx.post("http://localhost:11434/api/chat").mock(
        side_effect=[
            httpx.Response(
                200,
                content=(
                    b'{"message":{"role":"assistant","content":"Hel"},"done":false}\n'
                    b'{"message":{"role":"assistant","content":"lo"},"done":false}\n'
                    b'{"message":{"role":"assistant","content":""},"done":true,"done_reason":"length",'
                    b'"prompt_eval_count":3,"eval_count":9}\n'
                ),
            ),
            httpx.Response(404, json={"error": "model 'nope' not found, try pulling it first"}),
        ]
    )
    adapter = catalog.get("ollama")
    events = [e async for e in adapter.stream(OLLAMA, LLMRequest(model="m", messages=[user()]))]
    assert "".join(e.text for e in events if e.type == StreamEventType.TEXT) == "Hello"
    assert events[-1].finish_reason == FinishReason.LENGTH
    assert events[-2].usage.total_tokens == 12
    with pytest.raises(errors.ModelNotFoundError):
        await adapter.generate(OLLAMA, LLMRequest(model="nope", messages=[user()]))


def test_ollama_refuses_unsupported_tool_controls():
    adapter = catalog.get("ollama")
    with pytest.raises(errors.UnsupportedCapabilityError):
        adapter.build_payload(
            LLMRequest(
                model="m", messages=[user()], tools=[TOOL], tool_choice=ToolChoice(mode="required")
            ),
            stream=False,
        )


@respx.mock
async def test_ollama_discovery_uses_api_show_capabilities():
    respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(
            200, json={"models": [{"name": "qwen3:8b"}, {"name": "old:1b"}]}
        )
    )
    respx.post("http://localhost:11434/api/show", json={"model": "qwen3:8b"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "capabilities": ["completion", "tools", "thinking"],
                "model_info": {"general.architecture": "qwen3", "qwen3.context_length": 40960},
            },
        )
    )
    respx.post("http://localhost:11434/api/show", json={"model": "old:1b"}).mock(
        return_value=httpx.Response(200, json={"model_info": {}})
    )
    qwen, old = await catalog.get("ollama").list_models(OLLAMA)
    assert qwen.ref == "ollama:qwen3:8b" and qwen.context_window == 40960
    caps = qwen.capabilities
    assert (caps.chat, caps.tool_calling, caps.reasoning, caps.vision) == (True, True, True, False)
    assert old.capabilities.tool_calling is None  # older servers report nothing: unknown

    health = await catalog.get("ollama").health_check(OLLAMA)
    assert health.status == "ready" and health.models_available == 2


@respx.mock
async def test_ollama_offline_health():
    respx.get("http://localhost:11434/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    assert (await catalog.get("ollama").health_check(OLLAMA)).status == "offline"


async def test_public_only_policy_blocks_a_cloud_account_pointed_at_loopback():
    conn = Connection(
        provider="openai", base_url="https://127.0.0.1/v1", credential=KEY, policy=PUBLIC_ONLY
    )
    with pytest.raises(errors.EndpointNotAllowedError):
        await catalog.get("openai").generate(conn, LLMRequest(model="m", messages=[user()]))
