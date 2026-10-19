"""Deleting a workflow (ADR-033): its run history goes with it, the tasks its
steps ran stay, and a run still waiting on someone blocks the delete."""

import respx
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import API_KEY_HEADER
from app.models import AuditLog, Task, WorkflowRun, WorkflowStepRun
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login, make_node
from tests.test_workflow_approval import STEPS
from tests.test_workflow_run import agent_reply, create_workflow, run_workflow


async def _count(db_session: AsyncSession, model) -> int:
    return (await db_session.execute(select(func.count()).select_from(model))).scalar_one()


@respx.mock(assert_all_mocked=False)
async def test_delete_removes_workflow_and_runs_but_keeps_tasks(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "worker", role="hybrid")
    respx_mock.post("http://worker:8010/execute").mock(side_effect=[agent_reply("done")])
    token = await login(client, OPERATOR_EMAIL)
    workflow = await create_workflow(
        client, token, "one-shot", [{"id": "only", "kind": "task", "prompt": "Do: {{input}}"}]
    )
    run = await run_workflow(client, token, workflow["id"], "water the plants")
    assert run["status"] == "succeeded"
    tasks_before = await _count(db_session, Task)

    response = await client.delete(f"/api/v1/workflows/{workflow['id']}", headers=bearer(token))
    assert response.status_code == 204, response.text

    gone = await client.get(f"/api/v1/workflows/{workflow['id']}", headers=bearer(token))
    assert gone.status_code == 404
    listed = await client.get("/api/v1/workflows", headers=bearer(token))
    assert listed.json() == []
    assert await _count(db_session, WorkflowRun) == 0
    assert await _count(db_session, WorkflowStepRun) == 0
    # the task the step ran is the caller's own history, not the workflow's
    assert await _count(db_session, Task) == tasks_before

    row = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "workflow.delete"))
    ).scalar_one()
    assert row.resource_id == workflow["id"]
    assert row.detail == {"name": "one-shot", "runs": 1}
    assert row.actor_user_id is not None

    # the name is free again
    await create_workflow(client, token, "one-shot", [{"id": "x", "kind": "task", "prompt": "y"}])


@respx.mock(assert_all_mocked=False)
async def test_paused_run_blocks_delete(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "worker", role="hybrid")
    respx_mock.post("http://worker:8010/execute").mock(side_effect=[agent_reply("draft")])
    token = await login(client, ADMIN_EMAIL)
    workflow = await create_workflow(client, token, "gated", STEPS)
    run = await run_workflow(client, token, workflow["id"], "release notes")
    assert run["status"] == "paused"

    response = await client.delete(f"/api/v1/workflows/{workflow['id']}", headers=bearer(token))
    assert response.status_code == 409
    assert "approve or reject" in response.text.lower()
    still = await client.get(f"/api/v1/workflows/{workflow['id']}", headers=bearer(token))
    assert still.status_code == 200
    assert await _count(db_session, WorkflowRun) == 1


async def test_delete_unknown_workflow_is_404(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    response = await client.delete(
        "/api/v1/workflows/00000000-0000-0000-0000-000000000000", headers=bearer(token)
    )
    assert response.status_code == 404


async def test_delete_requires_operator_or_admin(
    client: AsyncClient, users: dict, node_api_key
) -> None:
    token = await login(client, ADMIN_EMAIL)
    workflow = await create_workflow(
        client, token, "kept", [{"id": "a", "kind": "task", "prompt": "b"}]
    )
    path = f"/api/v1/workflows/{workflow['id']}"
    assert (await client.delete(path)).status_code == 401
    full_key, _ = node_api_key
    assert (await client.delete(path, headers={API_KEY_HEADER: full_key})).status_code == 403
    assert (await client.get(path, headers=bearer(token))).status_code == 200
