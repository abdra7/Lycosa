"""Free-only direct cloud path, persistence, credential boundaries and workflow wiring."""

import json

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.schemas.task import TaskCreate
from app.schemas.workflow import TaskStepDef
from app.services import openrouter, provider_secrets, workflow
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    monkeypatch.setattr(provider_secrets, "_session_keys", {})


@pytest.mark.parametrize(
    "scenario",
    [
        "ok",
        "private",
        "paid",
        "missing",
        "rate_limit",
        "redirect",
        "malformed",
        "empty",
        "truncated",
    ],
)
async def test_openrouter_api(client, users, monkeypatch, scenario):
    key = "test-only-openrouter-credential"
    monkeypatch.setattr(
        openrouter, "provider_key", lambda _: None if scenario == "missing" else key
    )
    token = await login(client, ADMIN_EMAIL)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(openrouter.ENDPOINT).mock(
            return_value=httpx.Response(
                429 if scenario == "rate_limit" else 302 if scenario == "redirect" else 200,
                headers={"Location": "https://never-follow.invalid"},
                json={}
                if scenario == "malformed"
                else {
                    "choices": [
                        {
                            "message": {"content": "" if scenario == "empty" else f"4 {key}"},
                            "finish_reason": "length" if scenario == "truncated" else "stop",
                        }
                    ],
                    "usage": {"cost": 0, "prompt_tokens": 10, "secret": key},
                },
            )
        )
        response = await client.post(
            "/api/v1/tasks",
            headers=bearer(token),
            json={
                "prompt": "What is 2+2?",
                "type": "general",
                "provider": "openrouter",
                "model": "paid/model" if scenario == "paid" else openrouter.MODEL,
                "requires_privacy": scenario == "private",
                "max_tokens": 128,
            },
        )
        assert response.status_code == 201, response.text
        task = response.json()
        assert task["status"] == ("succeeded" if scenario == "ok" else "failed")
        assert key not in response.text
        assert route.call_count == (0 if scenario in {"private", "paid", "missing"} else 1)
        if route.called:
            sent = json.loads(route.calls.last.request.content)
            assert sent["model"] == openrouter.MODEL
            assert sent["provider"]["max_price"] == {"prompt": 0, "completion": 0}
            assert key not in str(sent)
        fetched = await client.get(f"/api/v1/tasks/{task['id']}", headers=bearer(token))
        assert fetched.json()["status"] == task["status"]
        assert key not in fetched.text
        audit = await client.get("/api/v1/admin/audit-logs", headers=bearer(token))
        assert key not in audit.text


async def test_admin_session_key_crud(client, users, monkeypatch):
    monkeypatch.setattr(provider_secrets, "_vault", lambda: (_ for _ in ()).throw(RuntimeError()))
    route = "/api/v1/admin/providers/openrouter/credential"
    key = "not-a-real-key"
    body = {"key": key, "storage": "session"}
    assert (await client.put(route, json=body)).status_code == 401
    operator = await login(client, OPERATOR_EMAIL)
    assert (await client.put(route, json=body, headers=bearer(operator))).status_code == 403
    admin = bearer(await login(client, ADMIN_EMAIL))
    assert (await client.put(route, json=body, headers=admin)).status_code == 204
    assert provider_secrets.provider_key("openrouter") == key
    response = await client.get("/api/v1/admin/providers", headers=admin)
    assert key not in response.text
    assert next(p for p in response.json() if p["name"] == "openrouter")["credential_configured"]
    assert (await client.delete(route + "?storage=session", headers=admin)).status_code == 204
    assert "openrouter" not in provider_secrets._session_keys
    monkeypatch.setattr(get_settings(), "workers", 2)
    assert (await client.put(route, json=body, headers=admin)).status_code == 503
    assert not provider_secrets._session_keys


async def test_key_validation_never_echoes_input(client, users):
    headers = bearer(await login(client, ADMIN_EMAIL))
    for value in ["", "test-key\r\nextra", " test-key ", "x" * 4097]:
        response = await client.put(
            "/api/v1/admin/providers/openrouter/credential",
            headers=headers,
            json={"key": value, "storage": "session"},
        )
        assert response.status_code == 422
        if value:
            assert value not in response.text


async def test_workflow_passes_provider_and_privacy(monkeypatch):
    from types import SimpleNamespace

    captured = []

    async def submit(db, body, user, key):
        captured.append(body)
        return SimpleNamespace(status="succeeded", result={"output": "4"}, id=None)

    monkeypatch.setattr(workflow, "submit_task", submit)
    step = TaskStepDef(
        id="answer",
        kind="task",
        prompt="{{input}}",
        provider="openrouter",
        model=openrouter.MODEL,
        requires_privacy=True,
        max_tokens=128,
    )
    await workflow._run_task(None, step, SimpleNamespace(input="2+2", context={}), None, None)
    assert captured[0].provider == "openrouter"
    assert captured[0].requires_privacy
    assert captured[0].max_tokens == 128


@respx.mock
async def test_timeout_is_sanitized(monkeypatch):
    monkeypatch.setattr(openrouter, "provider_key", lambda _: "fixture-key")
    respx.post(openrouter.ENDPOINT).mock(side_effect=httpx.ReadTimeout("fixture-key"))
    result = await openrouter.complete(
        TaskCreate(prompt="hi", provider="openrouter", model=openrouter.MODEL), "hi"
    )
    assert "error" in result and "fixture-key" not in str(result)
