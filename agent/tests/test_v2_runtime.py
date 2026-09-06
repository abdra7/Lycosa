import json
import sys
from types import SimpleNamespace

import httpx
import pytest
import respx

from lycosa_agent.executor import create_app
from lycosa_agent.gpu import collect_gpus
from lycosa_agent.runtimes.anthropic import AnthropicAdapter
from lycosa_agent.runtimes.chat import ChatMessage, ProviderError, RuntimeRequest
from lycosa_agent.runtimes.ollama import OllamaAdapter


def test_no_nvml_degrades(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", None)
    assert collect_gpus() == []


def test_nvml_multiple_devices_and_partial_metrics(monkeypatch):
    closed = []
    fake = SimpleNamespace(
        nvmlInit=lambda: None,
        nvmlShutdown=lambda: closed.append(True),
        nvmlDeviceGetCount=lambda: 2,
        nvmlDeviceGetHandleByIndex=lambda i: i,
        nvmlDeviceGetName=lambda i: f"GPU {i}",
        nvmlDeviceGetMemoryInfo=lambda i: SimpleNamespace(used=1024**3, total=4 * 1024**3),
        nvmlDeviceGetUtilizationRates=lambda i: SimpleNamespace(gpu=20 + i),
        nvmlDeviceGetTemperature=lambda *args: 60,
        NVML_TEMPERATURE_GPU=0,
    )
    monkeypatch.setitem(sys.modules, "pynvml", fake)
    values = collect_gpus()
    assert len(values) == 2
    assert values[1]["index"] == 1
    assert values[0]["memory_percent"] == 25
    assert closed == [True]


@respx.mock
async def test_anthropic_translates_and_redacts():
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "key=secret-value"}],
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
        )
    )
    result = await AnthropicAdapter().complete(
        RuntimeRequest(
            model="anthropic/test-model",
            messages=[
                ChatMessage(role="system", content="be brief"),
                ChatMessage(role="user", content="hello"),
            ],
        ),
        "secret-value",
    )
    payload = json.loads(route.calls[0].request.content)
    assert payload["system"] == "be brief"
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert payload["model"] == "test-model"
    assert result.output == "key=[REDACTED]"
    assert result.usage["input_tokens"] == 10


@respx.mock
async def test_provider_failure_does_not_include_remote_body():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(401, text="secret-value")
    )
    with pytest.raises(ProviderError, match="Cloud provider request failed") as error:
        await AnthropicAdapter().complete(
            RuntimeRequest(model="m", messages=[ChatMessage(role="user", content="hello")]),
            "secret-value",
        )
    assert "secret-value" not in str(error.value)


@respx.mock
async def test_ollama_chat_contract():
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "hello"}})
    )
    adapter = OllamaAdapter()
    try:
        result = await adapter.complete(
            RuntimeRequest(
                model="ollama/test", messages=[ChatMessage(role="user", content="hello")]
            )
        )
        assert result.output == "hello"
        assert json.loads(route.calls[0].request.content)["model"] == "test"
    finally:
        await adapter.aclose()


@respx.mock
async def test_cloud_execute_is_opt_in_and_resets_active_count():
    adapter = OllamaAdapter()
    payload = {"provider": "anthropic", "model": "test-model", "prompt": "hi"}
    headers = {"X-Agent-Token": "node-token", "X-Provider-Credential": "private-key"}
    app = create_app(adapter, "node-token")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://agent"
    ) as client:
        response = await client.post("/execute", json=payload, headers=headers)
        assert response.status_code == 403
    app = create_app(adapter, "node-token", cloud_enabled=True)

    def complete(request):
        assert app.state.running_tasks == 1
        return httpx.Response(200, json={"content": [{"type": "text", "text": "done"}]})

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=complete)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://agent"
        ) as client:
            response = await client.post("/execute", json=payload, headers=headers)
            assert response.json()["output"] == "done"
            assert app.state.running_tasks == 0
            assert "private-key" not in repr(app.state._state)
            refused = await client.post(
                "/execute", json={**payload, "agent_loop": True}, headers=headers
            )
            assert refused.status_code == 422
    finally:
        await adapter.aclose()
