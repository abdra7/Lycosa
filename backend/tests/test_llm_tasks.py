"""Tasks and workflow steps through the universal LLM layer, alongside the
unchanged legacy routes."""

import json

import httpx
import pytest
import respx
from pydantic import ValidationError
from sqlalchemy import select

from app.llm import discovery, gateway
from app.models import LLMUsage
from app.schemas.task import TaskCreate
from app.schemas.workflow import TaskStepDef
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login
from tests.llm_helpers import (
    TEST_KEY,
    anthropic_message,
    make_account,
    make_route,
    openai_completion,
)

OPENAI = "https://api.openai.com/v1/chat/completions"
ANTHROPIC = "https://api.anthropic.com/v1/messages"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(gateway, "sleep", no_sleep)
    discovery.invalidate()


@pytest.fixture
async def operator(client, users):
    return bearer(await login(client, OPERATOR_EMAIL))


@pytest.mark.parametrize(
    "fields",
    [
        {"llm_account_id": "00000000-0000-0000-0000-000000000001"},  # no model
        {
            "llm_account_id": "00000000-0000-0000-0000-000000000001",
            "model": "m",
            "route": "default",
        },
        {"route": "default", "provider": "openai"},
        {"route": "fastest"},
    ],
)
def test_task_and_step_validation(fields):
    with pytest.raises(ValidationError):
        TaskCreate(prompt="x", **fields)
    with pytest.raises(ValidationError):
        TaskStepDef(id="s", kind="task", prompt="x", **fields)


def test_legacy_task_shape_is_unchanged():
    task = TaskCreate(prompt="x")
    assert task.provider == "ollama" and not task.uses_llm_layer


@respx.mock
async def test_task_on_an_explicit_account(client, operator, users, db_session):
    account = await make_account(db_session, "anthropic", owner=users["operator"].id)
    route = respx.post(ANTHROPIC).mock(return_value=httpx.Response(200, json=anthropic_message()))
    response = await client.post(
        "/api/v1/tasks",
        headers=operator,
        json={"prompt": "summarize", "llm_account_id": str(account.id), "model": "claude-x"},
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["status"] == "succeeded" and task["result"]["output"] == "claude answer"
    assert task["result"]["model"] == "anthropic:claude-x"
    routing = task["result"]["routing"]
    assert routing["account_id"] == str(account.id) and routing["execution_mode"] == "cloud"
    assert task["payload"]["llm_account_id"] == str(account.id)
    assert TEST_KEY not in response.text
    sent = json.loads(route.calls.last.request.content)
    assert "temperature" not in sent  # not chosen explicitly: the provider default applies
    assert sent["max_tokens"] == 4096
    usage = (await db_session.execute(select(LLMUsage))).scalar_one()
    assert str(usage.task_id) == task["id"]


@respx.mock
async def test_explicit_temperature_is_passed_through(client, operator, users, db_session):
    account = await make_account(db_session, "openai", owner=users["operator"].id)
    route = respx.post(OPENAI).mock(return_value=httpx.Response(200, json=openai_completion()))
    await client.post(
        "/api/v1/tasks",
        headers=operator,
        json={"prompt": "x", "llm_account_id": str(account.id), "model": "m", "temperature": 0.7},
    )
    assert json.loads(route.calls.last.request.content)["temperature"] == 0.7


@respx.mock
async def test_task_on_a_route_with_fallback(client, operator, db_session):
    primary = await make_account(db_session, "openai", label="org-openai")
    backup = await make_account(db_session, "anthropic", label="org-claude")
    await make_route(db_session, "default", [(primary, "gpt-x"), (backup, "claude-x")])
    respx.post(OPENAI).mock(return_value=httpx.Response(429))
    respx.post(ANTHROPIC).mock(
        return_value=httpx.Response(200, json=anthropic_message("via fallback"))
    )
    task = (
        await client.post(
            "/api/v1/tasks", headers=operator, json={"prompt": "x", "route": "default"}
        )
    ).json()
    assert task["status"] == "succeeded" and task["result"]["output"] == "via fallback"
    assert task["result"]["routing"]["fallback_index"] == 1
    assert task["result"]["routing"]["skipped"][0]["error"] == "provider_rate_limited"


@respx.mock
async def test_auto_route_follows_the_task_type(client, operator, db_session):
    general = await make_account(db_session, "openai", label="general")
    coder = await make_account(db_session, "mistral", label="coder")
    await make_route(db_session, "default", [(general, "g")])
    await make_route(db_session, "coding", [(coder, "c")])
    respx.post("https://api.mistral.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=openai_completion("code"))
    )
    task = (
        await client.post(
            "/api/v1/tasks",
            headers=operator,
            json={"prompt": "write a function", "type": "coding", "route": "auto"},
        )
    ).json()
    assert task["result"]["routing"]["purpose"] == "coding"
    assert task["result"]["model"] == "mistral:c"


async def test_failures_are_recorded_on_the_task(client, operator, users, db_session):
    other = await make_account(db_session, "openai", owner=users["admin"].id)
    response = await client.post(
        "/api/v1/tasks",
        headers=operator,
        json={"prompt": "x", "llm_account_id": str(other.id), "model": "m"},
    )
    task = response.json()
    assert task["status"] == "failed" and task["error"] == "Account not found"
    no_route = (
        await client.post("/api/v1/tasks", headers=operator, json={"prompt": "x", "route": "cheap"})
    ).json()
    assert no_route["status"] == "failed" and "No usable route" in no_route["error"]


@respx.mock
async def test_truncated_output_fails_the_task(client, operator, users, db_session):
    account = await make_account(db_session, "openai", owner=users["operator"].id)
    respx.post(OPENAI).mock(
        return_value=httpx.Response(200, json=openai_completion("half", finish="length"))
    )
    task = (
        await client.post(
            "/api/v1/tasks",
            headers=operator,
            json={"prompt": "x", "llm_account_id": str(account.id), "model": "m"},
        )
    ).json()
    assert task["status"] == "failed" and "max_tokens" in task["error"]


async def test_privacy_blocks_cloud_accounts_before_any_call(client, operator, users, db_session):
    account = await make_account(db_session, "openai", owner=users["operator"].id)
    with respx.mock(assert_all_called=False) as mock:
        task = (
            await client.post(
                "/api/v1/tasks",
                headers=operator,
                json={
                    "prompt": "secret",
                    "llm_account_id": str(account.id),
                    "model": "m",
                    "requires_privacy": True,
                },
            )
        ).json()
        assert not mock.calls
    assert task["status"] == "failed" and "Privacy" in task["error"]


@respx.mock
async def test_private_route_uses_only_the_local_runtime(client, operator, db_session):
    cloud = await make_account(db_session, "openai", label="cloud")
    local = await make_account(
        db_session,
        "ollama",
        label="lan",
        base_url="http://127.0.0.1:11434",
        key=None,
        is_local=True,
    )
    await make_route(db_session, "default", [(cloud, "g"), (local, "llama3.2:1b")])
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": "local answer"}, "done": True, "done_reason": "stop"},
        )
    )
    task = (
        await client.post(
            "/api/v1/tasks",
            headers=operator,
            json={"prompt": "secret", "route": "default", "requires_privacy": True},
        )
    ).json()
    assert task["status"] == "succeeded", task
    assert task["result"]["routing"]["execution_mode"] == "local"
    assert task["result"]["routing"]["skipped"] == []  # the cloud entry was never a candidate
    assert not any("openai" in str(c.request.url) for c in respx.calls)


@respx.mock
async def test_workflow_step_on_a_route(client, users, db_session):
    admin = bearer(await login(client, ADMIN_EMAIL))
    account = await make_account(db_session, "openai", label="wf")
    await make_route(db_session, "default", [(account, "gpt-x")])
    respx.post(OPENAI).mock(return_value=httpx.Response(200, json=openai_completion("step out")))
    created = await client.post(
        "/api/v1/workflows",
        headers=admin,
        json={
            "name": "llm-route-flow",
            "definition": {
                "steps": [
                    {"id": "s1", "kind": "task", "prompt": "do {{input}}", "route": "default"}
                ]
            },
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["definition"]["steps"][0]["route"] == "default"
    run = await client.post(
        f"/api/v1/workflows/{created.json()['id']}/run", headers=admin, json={"input": "it"}
    )
    assert run.status_code in (200, 201), run.text
    assert run.json()["status"] == "succeeded"
    assert run.json()["context"]["steps"]["s1"]["output"] == "step out"
