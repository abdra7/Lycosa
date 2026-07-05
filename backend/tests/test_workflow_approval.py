import respx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from tests.conftest import ADMIN_EMAIL, bearer, login, make_node
from tests.test_workflow_run import agent_reply, create_workflow, run_workflow

STEPS = [
    {"id": "draft", "kind": "task", "prompt": "Draft: {{input}}"},
    {"id": "gate", "kind": "approval", "message": "Review the draft: {{steps.draft.output}}"},
    {"id": "publish", "kind": "task", "prompt": "Publish: {{steps.draft.output}}"},
]


@respx.mock(assert_all_mocked=False)
async def test_run_pauses_at_approval_and_resumes_on_approve(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "worker", role="hybrid")
    respx_mock.post("http://worker:8010/execute").mock(
        side_effect=[agent_reply("the draft text"), agent_reply("published!")]
    )

    token = await login(client, ADMIN_EMAIL)
    workflow = await create_workflow(client, token, "gated-wf", STEPS)
    run = await run_workflow(client, token, workflow["id"], "release notes")

    # paused mid-run, trace shows the pending step with the rendered message
    assert run["status"] == "paused"
    assert run["current_step"] == "gate"
    pending = next(s for s in run["step_runs"] if s["step_id"] == "gate")
    assert pending["status"] == "pending_approval"
    assert "the draft text" in pending["output"]
    assert not any(s["step_id"] == "publish" for s in run["step_runs"])

    # status queryable while paused
    fetched = await client.get(
        f"/api/v1/workflows/{workflow['id']}/runs/{run['id']}", headers=bearer(token)
    )
    assert fetched.json()["status"] == "paused"

    # approve -> resumes and completes
    resumed = await client.post(
        f"/api/v1/workflows/{workflow['id']}/runs/{run['id']}/approve",
        json={"approved": True},
        headers=bearer(token),
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["status"] == "succeeded"
    statuses = {s["step_id"]: s["status"] for s in body["step_runs"]}
    assert statuses == {"draft": "succeeded", "gate": "succeeded", "publish": "succeeded"}
    assert body["context"]["steps"]["publish"]["output"] == "published!"

    actions = [row[0] for row in await db_session.execute(select(AuditLog.action))]
    assert "workflow.run.paused" in actions
    assert "workflow.run.approved" in actions


@respx.mock(assert_all_mocked=False)
async def test_rejection_fails_run(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "worker", role="hybrid")
    respx_mock.post("http://worker:8010/execute").mock(side_effect=[agent_reply("the draft text")])

    token = await login(client, ADMIN_EMAIL)
    workflow = await create_workflow(client, token, "rejected-wf", STEPS)
    run = await run_workflow(client, token, workflow["id"], "release notes")
    assert run["status"] == "paused"

    rejected = await client.post(
        f"/api/v1/workflows/{workflow['id']}/runs/{run['id']}/approve",
        json={"approved": False},
        headers=bearer(token),
    )
    body = rejected.json()
    assert body["status"] == "failed"
    assert "rejected" in body["error"]
    gate = next(s for s in body["step_runs"] if s["step_id"] == "gate")
    assert gate["status"] == "failed"

    actions = [row[0] for row in await db_session.execute(select(AuditLog.action))]
    assert "workflow.run.rejected" in actions


@respx.mock(assert_all_mocked=False)
async def test_approving_a_finished_run_is_409(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "worker", role="hybrid")
    respx_mock.post("http://worker:8010/execute").mock(side_effect=[agent_reply("done")])

    token = await login(client, ADMIN_EMAIL)
    workflow = await create_workflow(
        client, token, "no-gate-wf", [{"id": "only", "kind": "task", "prompt": "{{input}}"}]
    )
    run = await run_workflow(client, token, workflow["id"], "x")
    assert run["status"] == "succeeded"

    response = await client.post(
        f"/api/v1/workflows/{workflow['id']}/runs/{run['id']}/approve",
        json={"approved": True},
        headers=bearer(token),
    )
    assert response.status_code == 409


async def test_workflows_require_auth(client: AsyncClient, roles: dict) -> None:
    assert (await client.post("/api/v1/workflows", json={})).status_code == 401
    assert (await client.get("/api/v1/workflows")).status_code == 401
