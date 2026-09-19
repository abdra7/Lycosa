"""Real optional SDK translation, intercepted HTTP: no live provider/paid calls."""

import importlib.util
import json

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.schemas.provider import ProviderProfile
from app.schemas.task import TaskCreate
from app.services import provider_adapters

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("litellm") is None, reason="optional providers extra not installed"
)


async def test_real_openai_sdk_translation(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("DISABLE_AIOHTTP_TRANSPORT", "True")
    monkeypatch.setattr(
        get_settings(),
        "provider_profiles",
        {"openai": ProviderProfile(sdk_provider="openai", models=["gpt-4o-mini"])},
    )
    monkeypatch.setattr(provider_adapters, "provider_key", lambda _: "synthetic-only-key")
    with respx.mock as mock:
        route = mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-fixture",
                    "created": 1,
                    "object": "chat.completion",
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "synthetic insight"},
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                },
            )
        )
        outcome = await provider_adapters.complete(
            TaskCreate(prompt="synthetic", provider="openai", model="gpt-4o-mini"), "synthetic"
        )
    assert outcome.get("output") == "synthetic insight", outcome
    payload = json.loads(route.calls.last.request.content)
    assert payload["messages"] == [{"role": "user", "content": "synthetic"}]
    assert route.calls.last.request.headers["authorization"] == "Bearer synthetic-only-key"


async def test_real_gemini_sdk_translation(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("DISABLE_AIOHTTP_TRANSPORT", "True")
    monkeypatch.setattr(
        get_settings(),
        "provider_profiles",
        {"gemini": ProviderProfile(sdk_provider="gemini", models=["gemini-2.0-flash"])},
    )
    monkeypatch.setattr(provider_adapters, "provider_key", lambda _: "synthetic-gemini-key")
    with respx.mock as mock:
        route = mock.post(url__regex=r"https://generativelanguage\.googleapis\.com/.*").mock(
            return_value=httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "index": 0,
                            "finishReason": "STOP",
                            "content": {
                                "role": "model",
                                "parts": [{"text": "synthetic Gemini insight"}],
                            },
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 3,
                        "candidatesTokenCount": 2,
                        "totalTokenCount": 5,
                    },
                },
            )
        )
        outcome = await provider_adapters.complete(
            TaskCreate(prompt="synthetic", provider="gemini", model="gemini-2.0-flash"), "synthetic"
        )
    assert outcome.get("output") == "synthetic Gemini insight", outcome
    payload = json.loads(route.calls.last.request.content)
    assert payload["contents"][0]["parts"][0]["text"] == "synthetic"
