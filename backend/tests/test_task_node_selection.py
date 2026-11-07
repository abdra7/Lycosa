"""Explicit CLI node selection must never dispatch elsewhere."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task
from app.schemas.task import TaskCreate
from app.services.orchestrator import submit_task
from tests.conftest import ADMIN_EMAIL, bearer, login, make_node


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_selected_node_wins_over_higher_ranked_node(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "preferred", role="ai_compute", gpu_vram_gb=48)
    chosen = await make_node(db_session, "chosen", role="hybrid", gpu_vram_gb=8)
    other = respx_mock.post("http://preferred:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "wrong node"})
    )
    target = respx_mock.post("http://chosen:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "selected node"})
    )
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/tasks",
        json={"prompt": "Refactor this python function", "node_id": str(chosen.id)},
        headers=bearer(token),
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["status"] == "succeeded"
    assert task["node_id"] == str(chosen.id)
    assert task["payload"]["node_id"] == str(chosen.id)
    assert task["result"]["output"] == "selected node"
    assert target.call_count == 1
    assert other.call_count == 0


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_selected_node_failure_does_not_fail_over(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    chosen = await make_node(db_session, "chosen", role="ai_compute")
    await make_node(db_session, "backup", role="ai_compute")
    target = respx_mock.post("http://chosen:8010/execute").mock(
        side_effect=httpx.ConnectError("selected node unreachable")
    )
    other = respx_mock.post("http://backup:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "wrong node"})
    )
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/tasks",
        json={"prompt": "Refactor this python function", "node_id": str(chosen.id)},
        headers=bearer(token),
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["status"] == "failed"
    assert len(task["executions"]) == 1
    assert task["executions"][0]["node_id"] == str(chosen.id)
    assert target.call_count == 1
    assert other.call_count == 0


@pytest.mark.parametrize("unavailable", ["offline", "stale", "missing", "wrong_role"])
@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_unavailable_selection_is_not_replaced(
    unavailable: str, respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "available", role="ai_compute")
    chosen_id = uuid.uuid4()
    if unavailable != "missing":
        chosen = await make_node(
            db_session,
            "chosen",
            role="storage" if unavailable == "wrong_role" else "ai_compute",
            status="offline" if unavailable == "offline" else "online",
        )
        chosen_id = chosen.id
        if unavailable == "stale":
            chosen.last_heartbeat_at = datetime.now(UTC) - timedelta(days=1)
            await db_session.commit()
    other = respx_mock.post("http://available:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "wrong node"})
    )
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/tasks",
        json={"prompt": "Refactor this python function", "node_id": str(chosen_id)},
        headers=bearer(token),
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["status"] == "failed"
    assert "Selected node" in task["error"]
    assert task["executions"] == []
    assert other.call_count == 0


@pytest.mark.parametrize(
    "settings",
    [
        {"route": "default"},
        {"llm_account_id": str(uuid.uuid4()), "model": "default"},
        {"provider": "openrouter"},
        {"provider": "openai"},
    ],
)
async def test_node_selection_rejects_routes_that_bypass_nodes(
    settings: dict, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/tasks",
        json={"prompt": "hello", "node_id": str(uuid.uuid4()), **settings},
        headers=bearer(token),
    )
    assert response.status_code == 422, response.text
    assert (await db_session.execute(select(Task))).first() is None


@respx.mock(assert_all_mocked=False)
async def test_context_changes_execution_but_keeps_original_prompt(
    respx_mock, db_session: AsyncSession
) -> None:
    chosen = await make_node(db_session, "chosen", role="ai_compute")
    target = respx_mock.post("http://chosen:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "continued"})
    )
    task = await submit_task(
        db_session,
        TaskCreate(prompt="Continue", type="coding", node_id=chosen.id),
        created_by_user_id=None,
        created_by_api_key_id=None,
        execution_prompt="Earlier conversation: fix Python code.\nUser: Continue",
    )
    assert task.status == "succeeded"
    assert task.payload["prompt"] == "Continue"
    assert json.loads(target.calls.last.request.content)["prompt"] == (
        "Earlier conversation: fix Python code.\nUser: Continue"
    )
