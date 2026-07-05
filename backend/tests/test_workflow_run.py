import respx
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import ADMIN_EMAIL, bearer, login, make_node
from tests.test_knowledge_ingest import create_collection, upload


async def create_workflow(client: AsyncClient, token: str, name: str, steps: list[dict]) -> dict:
    response = await client.post(
        "/api/v1/workflows",
        json={"name": name, "definition": {"steps": steps}},
        headers=bearer(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def run_workflow(client: AsyncClient, token: str, workflow_id: str, run_input: str) -> dict:
    response = await client.post(
        f"/api/v1/workflows/{workflow_id}/run",
        json={"input": run_input},
        headers=bearer(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def agent_reply(text: str) -> Response:
    return Response(200, json={"status": "succeeded", "output": text})


@respx.mock(assert_all_mocked=False)
async def test_sdd_planner_coder_test_review_chain(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "worker", role="hybrid")
    route = respx_mock.post("http://worker:8010/execute").mock(
        side_effect=[
            agent_reply("PLAN: build a CSV parser"),
            agent_reply("CODE: def parse_csv(): ..."),
            agent_reply("PASS: 12 tests green"),
            agent_reply("LGTM, ship it"),
        ]
    )

    token = await login(client, ADMIN_EMAIL)
    workflow = await create_workflow(
        client,
        token,
        "build-feature",
        [
            {"id": "plan", "kind": "task", "prompt": "Plan how to: {{input}}"},
            {"id": "code", "kind": "task", "prompt": "Implement:\n{{steps.plan.output}}"},
            {"id": "test", "kind": "task", "prompt": "Test this:\n{{steps.code.output}}"},
            {
                "id": "review",
                "kind": "task",
                "prompt": "Review:\n{{steps.code.output}}",
                "when": {"step": "test", "contains": "PASS"},
            },
        ],
    )
    run = await run_workflow(client, token, workflow["id"], "parse CSV files")

    assert run["status"] == "succeeded"
    statuses = {s["step_id"]: s["status"] for s in run["step_runs"]}
    assert statuses == {
        "plan": "succeeded",
        "code": "succeeded",
        "test": "succeeded",
        "review": "succeeded",
    }
    # context propagated: each dispatched prompt carries the prior output
    sent = [call.request.read().decode() for call in route.calls]
    assert "parse CSV files" in sent[0]
    assert "PLAN: build a CSV parser" in sent[1]
    assert "CODE: def parse_csv()" in sent[2]
    assert run["context"]["steps"]["review"]["output"] == "LGTM, ship it"
    # every task step links to its full Phase-5 task trace
    assert all(s["task_id"] for s in run["step_runs"])

    # trace queryable afterwards
    fetched = await client.get(
        f"/api/v1/workflows/{workflow['id']}/runs/{run['id']}", headers=bearer(token)
    )
    assert fetched.status_code == 200
    assert len(fetched.json()["step_runs"]) == 4


@respx.mock(assert_all_mocked=False)
async def test_unmet_condition_skips_step(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "worker", role="hybrid")
    respx_mock.post("http://worker:8010/execute").mock(
        side_effect=[agent_reply("FAIL: 3 tests broken")]
    )

    token = await login(client, ADMIN_EMAIL)
    workflow = await create_workflow(
        client,
        token,
        "branchy",
        [
            {"id": "test", "kind": "task", "prompt": "Run tests for {{input}}"},
            {
                "id": "ship",
                "kind": "task",
                "prompt": "Ship it",
                "when": {"step": "test", "contains": "PASS"},
            },
        ],
    )
    run = await run_workflow(client, token, workflow["id"], "the parser")

    assert run["status"] == "succeeded"
    statuses = {s["step_id"]: s["status"] for s in run["step_runs"]}
    assert statuses == {"test": "succeeded", "ship": "skipped"}


@respx.mock(assert_all_mocked=False)
async def test_step_retries_then_succeeds(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "worker", role="hybrid")
    respx_mock.post("http://worker:8010/execute").mock(
        side_effect=[
            Response(200, json={"status": "failed", "error": "model hiccup"}),
            agent_reply("recovered fine"),
        ]
    )

    token = await login(client, ADMIN_EMAIL)
    workflow = await create_workflow(
        client,
        token,
        "retry-wf",
        [{"id": "flaky", "kind": "task", "prompt": "Do {{input}}", "retries": 1}],
    )
    run = await run_workflow(client, token, workflow["id"], "something")

    assert run["status"] == "succeeded"
    attempts = [(s["attempt"], s["status"]) for s in run["step_runs"]]
    assert attempts == [(1, "failed"), (2, "succeeded")]


@respx.mock(assert_all_mocked=False)
async def test_step_failure_after_retries_fails_run(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "worker", role="hybrid")
    respx_mock.post("http://worker:8010/execute").mock(
        return_value=Response(200, json={"status": "failed", "error": "always broken"})
    )

    token = await login(client, ADMIN_EMAIL)
    workflow = await create_workflow(
        client,
        token,
        "doomed",
        [{"id": "boom", "kind": "task", "prompt": "Do {{input}}", "retries": 1}],
    )
    run = await run_workflow(client, token, workflow["id"], "anything")

    assert run["status"] == "failed"
    assert "boom" in run["error"]
    assert len(run["step_runs"]) == 2  # both attempts traced


@respx.mock(assert_all_mocked=False)
async def test_retrieve_step_feeds_task_step(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict, qdrant
) -> None:
    token = await login(client, ADMIN_EMAIL)
    collection = await create_collection(client, token, name="wf-facts")
    await upload(
        client,
        token,
        collection["id"],
        "facts.txt",
        b"Lycosa wolf spiders carry their spiderlings on their backs.",
    )
    await make_node(db_session, "worker", role="hybrid")
    route = respx_mock.post("http://worker:8010/execute").mock(
        side_effect=[agent_reply("summarized")]
    )

    workflow = await create_workflow(
        client,
        token,
        "rag-wf",
        [
            {"id": "ctx", "kind": "retrieve", "query": "{{input}}", "top_k": 2},
            {"id": "answer", "kind": "task", "prompt": "Answer using:\n{{steps.ctx.output}}"},
        ],
    )
    run = await run_workflow(client, token, workflow["id"], "spiderlings on backs")

    assert run["status"] == "succeeded"
    dispatched = route.calls.last.request.read().decode()
    assert "spiderlings on their backs" in dispatched


@respx.mock(assert_all_mocked=False)
async def test_parallel_substeps_both_recorded(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    await make_node(db_session, "worker", role="hybrid")
    respx_mock.post("http://worker:8010/execute").mock(
        side_effect=[agent_reply("alpha done"), agent_reply("beta done")]
    )

    token = await login(client, ADMIN_EMAIL)
    workflow = await create_workflow(
        client,
        token,
        "fanout-wf",
        [
            {
                "id": "fan",
                "kind": "parallel",
                "steps": [
                    {"id": "alpha", "kind": "task", "prompt": "Do alpha for {{input}}"},
                    {"id": "beta", "kind": "task", "prompt": "Do beta for {{input}}"},
                ],
            },
            {
                "id": "join",
                "kind": "task",
                "prompt": "Combine: {{steps.alpha.output}} + {{steps.beta.output}}",
            },
        ],
    )
    # the join step needs a third response
    respx_mock.post("http://worker:8010/execute").mock(
        side_effect=[agent_reply("alpha done"), agent_reply("beta done"), agent_reply("combined")]
    )
    run = await run_workflow(client, token, workflow["id"], "the release")

    assert run["status"] == "succeeded", run["error"]
    outputs = {sid: s["output"] for sid, s in run["context"]["steps"].items()}
    assert set(outputs) >= {"alpha", "beta", "fan", "join"}
    assert sorted([outputs["alpha"], outputs["beta"]]) == ["alpha done", "beta done"]
