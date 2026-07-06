"""End-to-end task flow with respx-mocked agent exec APIs.

respx patches httpx's real transports (which the orchestrator uses) but not
the ASGI transport the test client uses, so app traffic flows normally.
"""

import asyncio
from contextlib import suppress

import httpx
import respx
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskStatus
from tests.conftest import ADMIN_EMAIL, bearer, login, make_node

TOKEN_HEADER = "X-Agent-Token"


async def _submit(client: AsyncClient, token: str, **overrides) -> dict:
    body = {"prompt": "Refactor this python function", **overrides}
    response = await client.post("/api/v1/tasks", json=body, headers=bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


@respx.mock(assert_all_mocked=False)
async def test_task_succeeds_on_compatible_node(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    node = await make_node(db_session, "gpu-box", role="ai_compute", models=["llama3:8b"])
    route = respx_mock.post("http://gpu-box:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "def fixed(): ..."})
    )

    token = await login(client, ADMIN_EMAIL)
    task = await _submit(client, token)

    assert task["type"] == "coding"
    assert task["status"] == "succeeded"
    assert task["result"]["output"] == "def fixed(): ..."
    assert task["node_id"] == str(node.id)
    assert len(task["executions"]) == 1
    assert task["executions"][0]["status"] == "succeeded"

    # agent got the token and the selected model
    request = route.calls.last.request
    assert request.headers[TOKEN_HEADER] == "test-agent-token-0123456789abcdef"
    assert b"llama3:8b" in request.read()

    # trace retrievable afterwards
    fetched = await client.get(f"/api/v1/tasks/{task['id']}", headers=bearer(token))
    assert fetched.status_code == 200
    assert fetched.json()["executions"][0]["output"] == "def fixed(): ..."


async def test_no_compatible_node_fails_gracefully(
    client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "storage-only", role="storage")  # wrong role

    token = await login(client, ADMIN_EMAIL)
    task = await _submit(client, token)

    assert task["status"] == "failed"
    assert "No compatible online node" in task["error"]
    assert task["executions"] == []


@respx.mock(assert_all_mocked=False)
async def test_failover_to_second_node(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    # 'strong' ranks first (more VRAM) but its agent is dead
    strong = await make_node(db_session, "strong", role="ai_compute", gpu_vram_gb=48)
    backup = await make_node(db_session, "backup", role="ai_compute", gpu_vram_gb=8)
    respx_mock.post("http://strong:8010/execute").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    respx_mock.post("http://backup:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "done by backup"})
    )

    token = await login(client, ADMIN_EMAIL)
    task = await _submit(client, token)

    assert task["status"] == "succeeded"
    assert task["result"]["output"] == "done by backup"
    assert task["node_id"] == str(backup.id)

    executions = task["executions"]
    assert len(executions) == 2
    assert executions[0]["node_id"] == str(strong.id)
    assert executions[0]["status"] == "failed"
    assert "connection refused" in executions[0]["error"]
    assert executions[1]["node_id"] == str(backup.id)
    assert executions[1]["status"] == "succeeded"


@respx.mock(assert_all_mocked=False)
async def test_all_nodes_failing_fails_task_with_trace(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "dead-1", role="tool")
    await make_node(db_session, "dead-2", role="tool")
    respx_mock.post("http://dead-1:8010/execute").mock(side_effect=httpx.ConnectError("refused"))
    respx_mock.post("http://dead-2:8010/execute").mock(side_effect=httpx.ConnectError("refused"))

    token = await login(client, ADMIN_EMAIL)
    task = await _submit(client, token, prompt="execute command ls", type="tool")

    assert task["status"] == "failed"
    assert "attempt(s) failed" in task["error"]
    assert len(task["executions"]) == 2
    assert all(e["status"] == "failed" for e in task["executions"])


@respx.mock(assert_all_mocked=False)
async def test_agent_reported_failure_triggers_failover(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "oom-box", role="ai_compute", gpu_vram_gb=48)
    await make_node(db_session, "ok-box", role="ai_compute", gpu_vram_gb=8)
    respx_mock.post("http://oom-box:8010/execute").mock(
        return_value=Response(200, json={"status": "failed", "error": "model OOM"})
    )
    respx_mock.post("http://ok-box:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "ok"})
    )

    token = await login(client, ADMIN_EMAIL)
    task = await _submit(client, token)

    assert task["status"] == "succeeded"
    assert task["executions"][0]["error"] == "model OOM"


@respx.mock(assert_all_mocked=False)
async def test_client_disconnect_does_not_lose_completion(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    """Ticket #102: when the dashboard's HTTP timeout drops the connection
    mid-dispatch, the agent still finishes the work — the controller must
    record the terminal state instead of leaving the task 'running' forever."""
    await make_node(db_session, "slow-box", role="ai_compute")

    async def slow_success(request: httpx.Request) -> Response:
        await asyncio.sleep(0.3)
        return Response(200, json={"status": "succeeded", "output": "late but done"})

    respx_mock.post("http://slow-box:8010/execute").mock(side_effect=slow_success)

    token = await login(client, ADMIN_EMAIL)
    request_task = asyncio.create_task(
        client.post(
            "/api/v1/tasks",
            json={"prompt": "Refactor this python function"},
            headers=bearer(token),
        )
    )
    await asyncio.sleep(0.15)  # the dispatch to the agent is now in flight
    request_task.cancel()  # simulates the client timing out / disconnecting
    with suppress(asyncio.CancelledError):
        await request_task

    # the dispatch must survive the disconnect and record the outcome
    task_row = None
    for _ in range(40):
        await asyncio.sleep(0.1)
        db_session.expire_all()
        task_row = (await db_session.execute(select(Task))).scalar_one_or_none()
        if task_row is not None and task_row.status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED):
            break
    assert task_row is not None, "task row was never created"
    assert task_row.status == TaskStatus.SUCCEEDED
    assert task_row.finished_at is not None
    assert task_row.result["output"] == "late but done"


async def test_tasks_require_operator_auth(client: AsyncClient, roles: dict) -> None:
    response = await client.post("/api/v1/tasks", json={"prompt": "hi"})
    assert response.status_code == 401


async def test_list_tasks_with_status_filter(
    client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    token = await login(client, ADMIN_EMAIL)
    # no nodes at all -> this task fails immediately
    await _submit(client, token)

    listed = await client.get("/api/v1/tasks?status=failed", headers=bearer(token))
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["status"] == "failed"
