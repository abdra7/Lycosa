from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.provider import ProviderProfile
from app.schemas.task import TaskCreate
from app.schemas.workflow import TaskStepDef
from app.services import provider_adapters as adapters
from app.services import provider_secrets
from app.services.provider_registry import SDK_PROVIDERS
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login

KEY = "synthetic-provider-secret"


def result(reason="stop", text="answer"):
    return {
        "choices": [{"finish_reason": reason, "message": {"content": text}}],
        "usage": {
            "prompt_tokens": 3,
            "completion_tokens": 4,
            "total_tokens": 7,
            "raw_content": "must-not-persist",
            "cost": 99,
        },
    }


@pytest.fixture
def adapter(monkeypatch):
    profiles = {p: ProviderProfile(sdk_provider=p, models=["test-model"]) for p in SDK_PROVIDERS}
    monkeypatch.setattr(get_settings(), "provider_profiles", profiles)
    monkeypatch.setattr(adapters, "provider_key", lambda _: KEY)
    completion = AsyncMock(return_value=result(text="answer " + KEY))
    monkeypatch.setattr(adapters, "_completion", completion)
    return completion


@pytest.mark.parametrize("provider", SDK_PROVIDERS)
async def test_all_builtin_sdk_routes_share_bounded_contract(adapter, provider):
    body = TaskCreate(prompt="synthetic", model="test-model", provider=provider)
    response = await adapters.complete(body, body.prompt)
    assert response["output"] == "answer [REDACTED]"
    assert response["usage"] == {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
    kwargs = adapter.call_args.kwargs
    assert kwargs["custom_llm_provider"] == provider
    assert kwargs["api_key"] == KEY
    assert kwargs["num_retries"] == 0
    assert kwargs["stream"] is False and kwargs["caching"] is False
    assert kwargs["drop_params"] is False
    assert "fallbacks" not in kwargs and "tools" not in kwargs
    assert TaskStepDef(id="a", kind="task", prompt="x", provider=provider).provider == provider


@pytest.mark.parametrize("private,model", [(True, "test-model"), (False, "not-allowed")])
async def test_policy_denies_without_provider_call(adapter, private, model):
    response = await adapters.complete(
        TaskCreate(prompt="x", provider="openai", model=model, requires_privacy=private), "x"
    )
    assert "error" in response
    adapter.assert_not_awaited()


@pytest.mark.parametrize("reason", ["length", "tool_calls", "content_filter", None])
async def test_incomplete_and_nontext_answers_are_not_retained(adapter, reason):
    adapter.return_value = result(reason=reason)
    response = await adapters.complete(
        TaskCreate(prompt="x", provider="openai", model="test-model"), "x"
    )
    assert "error" in response and "output" not in response


async def test_sdk_errors_do_not_echo_content_or_credentials(adapter):
    adapter.side_effect = RuntimeError("SENSITIVE " + KEY)
    response = await adapters.complete(
        TaskCreate(prompt="x", provider="openai", model="test-model"), "x"
    )
    assert "SENSITIVE" not in str(response) and KEY not in str(response)


async def test_custom_provider_alias_and_workload_identity(adapter, monkeypatch):
    monkeypatch.setattr(
        get_settings(),
        "provider_profiles",
        {
            "institution_vertex": ProviderProfile(
                sdk_provider="vertex_ai",
                models=["model"],
                auth="workload_identity",
                project="test-project",
                region="us-central1",
            )
        },
    )
    monkeypatch.setattr(adapters, "provider_key", lambda _: pytest.fail("must not read a key"))
    response = await adapters.complete(
        TaskCreate(prompt="x", provider="institution_vertex", model="model"), "x"
    )
    assert "output" in response
    kwargs = adapter.call_args.kwargs
    assert "api_key" not in kwargs
    assert kwargs["vertex_project"] == "test-project"
    assert kwargs["vertex_location"] == "us-central1"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://host",
        "https://user:secret@host",
        "https://host?api_key=secret",
        "https://host#secret",
    ],
)
def test_profile_rejects_unprotected_or_credential_endpoints(endpoint):
    with pytest.raises(ValidationError):
        ProviderProfile(sdk_provider="openai", models=["m"], api_base=endpoint)


async def test_new_provider_task_persists_and_private_route_stays_denied(client, users, adapter):
    token = await login(client, ADMIN_EMAIL)
    for private in [False, True]:
        response = await client.post(
            "/api/v1/tasks",
            headers=bearer(token),
            json={
                "prompt": "synthetic",
                "model": "test-model",
                "provider": "gemini",
                "requires_privacy": private,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == ("failed" if private else "succeeded")
        assert KEY not in response.text
    assert adapter.await_count == 1


async def test_operator_catalog_and_admin_session_keys(client, users, adapter, monkeypatch):
    operator = await login(client, OPERATOR_EMAIL)
    response = await client.get("/api/v1/providers", headers=bearer(operator))
    assert response.status_code == 200
    assert KEY not in response.text
    assert "api_base" not in response.text
    assert set(SDK_PROVIDERS) <= {p["name"] for p in response.json()}
    route = "/api/v1/admin/providers/openai/credential"
    assert (
        await client.put(route, headers=bearer(operator), json={"key": KEY, "storage": "session"})
    ).status_code == 403
    admin = await login(client, ADMIN_EMAIL)
    monkeypatch.setattr(get_settings(), "workers", 1)
    try:
        response = await client.put(
            route, headers=bearer(admin), json={"key": KEY, "storage": "session"}
        )
        assert response.status_code == 204
        assert provider_secrets.provider_key("openai") == KEY
        assert (
            await client.delete(route + "?storage=session", headers=bearer(admin))
        ).status_code == 204
        assert "openai" not in provider_secrets._session_keys
    finally:
        provider_secrets._session_keys.pop("openai", None)


async def test_sdk_request_logging_is_scoped_and_reset(adapter):
    import logging

    from app.core.logging import JsonFormatter, provider_request_var

    async def attempt(**kwargs):
        assert provider_request_var.get() is True
        record = logging.LogRecord("httpx", logging.INFO, "", 0, KEY, (), None)
        handlers = [
            h for h in logging.getLogger().handlers if isinstance(h.formatter, JsonFormatter)
        ]
        assert handlers
        assert all(not h.filter(record) for h in handlers)
        raise RuntimeError(KEY)

    adapter.side_effect = attempt
    assert provider_request_var.get() is False
    response = await adapters.complete(
        TaskCreate(prompt="x", provider="openai", model="test-model"), "x"
    )
    assert "error" in response and KEY not in str(response)
    assert provider_request_var.get() is False
