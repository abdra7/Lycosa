"""Real API/dispatcher coverage for durable, isolated CLI workspaces."""

import asyncio
import json
import uuid
from contextlib import suppress

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models import Task, TaskStatus
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login, make_node


@pytest.fixture
async def auth(client, users):
    return bearer(await login(client, ADMIN_EMAIL))


async def workspace(client, auth, name="Project"):
    response = await client.post("/api/v1/cli/workspaces", headers=auth, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def session(client, auth, workspace_id, **fields):
    response = await client.post(
        f"/api/v1/cli/workspaces/{workspace_id}/sessions",
        headers=auth,
        json={"name": "Session 1", **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_workspace_session_lifecycle_and_owner_isolation(client, auth, users):
    w = await workspace(client, auth, "  Example  ")
    assert w["name"] == "Example"
    duplicate = await client.post("/api/v1/cli/workspaces", headers=auth, json={"name": "example"})
    assert duplicate.status_code == 409
    s = await session(client, auth, w["id"])
    assert s["settings"]["provider"] == "ollama"
    assert s["tasks"] == [] and not s["archived"]
    path = f"/api/v1/cli/sessions/{s['id']}"
    archived = await client.patch(path, headers=auth, json={"archived": True})
    assert archived.status_code == 200 and archived.json()["archived"]
    blocked = await client.post(path + "/tasks", headers=auth, json={"prompt": "Hello"})
    assert blocked.status_code == 409
    restored = await client.patch(path, headers=auth, json={"archived": False, "name": "Renamed"})
    assert restored.status_code == 200 and restored.json()["name"] == "Renamed"
    listed = await client.get(f"/api/v1/cli/workspaces/{w['id']}/sessions", headers=auth)
    assert listed.json()[0]["name"] == "Renamed"

    other = bearer(await login(client, OPERATOR_EMAIL))
    assert (await client.get("/api/v1/cli/workspaces", headers=other)).json() == []
    for method, url, body in [
        ("GET", f"/api/v1/cli/workspaces/{w['id']}/sessions", None),
        ("POST", f"/api/v1/cli/workspaces/{w['id']}/sessions", {"name": "Sneak"}),
        ("PATCH", path, {"name": "Sneak"}),
        ("POST", path + "/tasks", {"prompt": "Sneak"}),
        ("DELETE", path, None),
    ]:
        response = await client.request(method, url, headers=other, json=body)
        assert response.status_code == 404, response.text
    # The same name is fine for another principal.
    await workspace(client, other, "Example")
    assert (await client.delete(path, headers=auth)).status_code == 204
    assert (
        await client.get(f"/api/v1/cli/workspaces/{w['id']}/sessions", headers=auth)
    ).json() == []


@respx.mock(assert_all_mocked=False)
async def test_real_dispatch_history_persistence_and_delete_keeps_tasks(
    respx_mock, client, auth, db_session
):
    node = await make_node(db_session, "cli-node", role="ai_compute")
    route = respx_mock.post("http://cli-node:8010/execute").mock(
        return_value=httpx.Response(200, json={"status": "succeeded", "output": "The name is Ada."})
    )
    w = await workspace(client, auth)
    s = await session(client, auth, w["id"], node_id=str(node.id))
    path = f"/api/v1/cli/sessions/{s['id']}"
    first = await client.post(path + "/tasks", headers=auth, json={"prompt": "Remember Ada"})
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "succeeded"
    second = await client.post(path + "/tasks", headers=auth, json={"prompt": "What is the name?"})
    assert second.status_code == 201, second.text
    assert second.json()["payload"]["prompt"] == "What is the name?"
    sent = json.loads(route.calls.last.request.content)
    assert "User: Remember Ada" in sent["prompt"]
    assert "Assistant: The name is Ada." in sent["prompt"]
    assert "User: What is the name?" in sent["prompt"]
    listed = await client.get(f"/api/v1/cli/workspaces/{w['id']}/sessions", headers=auth)
    tasks = listed.json()[0]["tasks"]
    assert len(tasks) == 2
    assert tasks[0]["executions"][0]["node_id"] == str(node.id)
    assert (await client.delete(path, headers=auth)).status_code == 204
    db_session.expire_all()
    rows = list((await db_session.scalars(select(Task))).all())
    assert len(rows) == 2 and all(t.cli_session_id is None for t in rows)


@respx.mock(assert_all_mocked=False)
async def test_concurrent_submit_guard_and_different_sessions_run_together(
    respx_mock, client, auth, db_session
):
    node = await make_node(db_session, "slow-cli", role="ai_compute")
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow_response(request):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"status": "succeeded", "output": "done"})

    respx_mock.post("http://slow-cli:8010/execute").mock(side_effect=slow_response)
    w = await workspace(client, auth)
    one = await session(client, auth, w["id"], node_id=str(node.id))
    two = await session(client, auth, w["id"], name="Session 2", node_id=str(node.id))
    path = f"/api/v1/cli/sessions/{one['id']}"
    first = asyncio.create_task(client.post(path + "/tasks", headers=auth, json={"prompt": "One"}))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        duplicate = await client.post(path + "/tasks", headers=auth, json={"prompt": "Duplicate"})
        assert duplicate.status_code == 409, duplicate.text
        assert (await client.patch(path, headers=auth, json={"archived": True})).status_code == 409
        assert (await client.delete(path, headers=auth)).status_code == 409
        polled = await client.get(f"/api/v1/cli/workspaces/{w['id']}/sessions", headers=auth)
        active = next(s for s in polled.json() if s["id"] == one["id"])
        assert active["tasks"][0]["status"] == "running"
        entered.clear()
        second = asyncio.create_task(
            client.post(
                f"/api/v1/cli/sessions/{two['id']}/tasks", headers=auth, json={"prompt": "Two"}
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert calls == 2
        finally:
            release.set()
        assert (await second).status_code == 201
    finally:
        release.set()
    assert (await first).status_code == 201


@respx.mock(assert_all_mocked=False)
async def test_disconnect_preserves_session_link_and_completed_output(
    respx_mock, client, auth, db_session
):
    await make_node(db_session, "disconnect-cli", role="ai_compute")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_response(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"status": "succeeded", "output": "survived"})

    respx_mock.post("http://disconnect-cli:8010/execute").mock(side_effect=slow_response)
    w = await workspace(client, auth)
    s = await session(client, auth, w["id"])
    request = asyncio.create_task(
        client.post(
            f"/api/v1/cli/sessions/{s['id']}/tasks", headers=auth, json={"prompt": "Continue"}
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    request.cancel()
    with suppress(asyncio.CancelledError):
        await request
    release.set()
    for _ in range(50):
        listed = await client.get(f"/api/v1/cli/workspaces/{w['id']}/sessions", headers=auth)
        tasks = listed.json()[0]["tasks"]
        if tasks and tasks[0]["status"] == TaskStatus.SUCCEEDED:
            break
        await asyncio.sleep(0.02)
    assert tasks[0]["result"]["output"] == "survived"


async def test_validation_and_auth(client, auth, db_session, node_api_key):
    assert (await client.get("/api/v1/cli/workspaces")).status_code == 401
    assert (
        await client.get("/api/v1/cli/workspaces", headers={"X-API-Key": node_api_key[0]})
    ).status_code == 403
    w = await workspace(client, auth)
    path = f"/api/v1/cli/workspaces/{w['id']}/sessions"
    node = await make_node(db_session, "validation-cli")
    for fields in [
        {"name": "   "},
        {"name": "x", "node_id": str(uuid.uuid4())},
        {"name": "x", "node_id": str(node.id), "settings": {"route": "default"}},
        {"name": "x", "node_id": str(node.id), "settings": {"provider": "openai"}},
        {"name": "x", "settings": {"provider": "not-a-provider"}},
        {"name": "x", "settings": {"llm_account_id": str(uuid.uuid4())}},
    ]:
        response = await client.post(path, headers=auth, json=fields)
        assert response.status_code == 422, response.text
    s = await session(client, auth, w["id"])
    response = await client.post(
        f"/api/v1/cli/sessions/{s['id']}/tasks", headers=auth, json={"prompt": "  "}
    )
    assert response.status_code == 422
    response = await client.patch(
        f"/api/v1/cli/sessions/{s['id']}", headers=auth, json={"settings": None}
    )
    assert response.status_code == 422


async def test_removed_node_does_not_silently_unpin_session(client, auth, db_session):
    node = await make_node(db_session, "removed-cli")
    w = await workspace(client, auth)
    s = await session(client, auth, w["id"], node_id=str(node.id))
    await db_session.delete(node)
    await db_session.commit()
    path = f"/api/v1/cli/sessions/{s['id']}"
    response = await client.post(path + "/tasks", headers=auth, json={"prompt": "Hello"})
    assert response.status_code == 422
    listed = await client.get(f"/api/v1/cli/workspaces/{w['id']}/sessions", headers=auth)
    assert listed.json()[0]["node_id"] == str(node.id)
    assert (await client.patch(path, headers=auth, json={"archived": True})).status_code == 200
    assert (await client.patch(path, headers=auth, json={"node_id": None})).status_code == 200


async def test_cli_history_private_even_after_session_deletion(client, auth, users):
    other = bearer(await login(client, OPERATOR_EMAIL))
    w = await workspace(client, auth)
    s = await session(client, auth, w["id"])
    path = f"/api/v1/cli/sessions/{s['id']}"
    response = await client.post(path + "/tasks", headers=auth, json={"prompt": "Private context"})
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    legacy = await client.post("/api/v1/tasks", headers=auth, json={"prompt": "Shared legacy task"})
    assert legacy.status_code == 201
    for deleted in (False, True):
        if deleted:
            assert (await client.delete(path, headers=auth)).status_code == 204
        assert (await client.get(f"/api/v1/tasks/{task_id}", headers=other)).status_code == 404
        theirs = (await client.get("/api/v1/tasks", headers=other)).json()
        assert [t["id"] for t in theirs] == [legacy.json()["id"]]
        own = (await client.get("/api/v1/tasks", headers=auth)).json()
        assert task_id in {t["id"] for t in own}
        assert (await client.get(f"/api/v1/tasks/{task_id}", headers=auth)).status_code == 200


async def test_api_key_workspace_and_history_are_separate_from_user(
    client, auth, db_session, roles
):
    from app.core.security import generate_api_key
    from app.models import ApiKey

    full_key, prefix, key_hash = generate_api_key()
    key = ApiKey(
        key_prefix=prefix, key_hash=key_hash, name="automation", role_id=roles["operator"].id
    )
    db_session.add(key)
    await db_session.commit()
    api_auth = {"X-API-Key": full_key}
    w = await workspace(client, api_auth)
    s = await session(client, api_auth, w["id"])
    response = await client.post(
        f"/api/v1/cli/sessions/{s['id']}/tasks", headers=api_auth, json={"prompt": "Key-owned"}
    )
    assert response.status_code == 201
    task_id = response.json()["id"]
    assert (await client.get("/api/v1/cli/workspaces", headers=auth)).json() == []
    assert (await client.get("/api/v1/tasks", headers=auth)).json() == []
    assert (await client.get(f"/api/v1/tasks/{task_id}", headers=auth)).status_code == 404
    assert (await client.get(f"/api/v1/tasks/{task_id}", headers=api_auth)).status_code == 200


@respx.mock(assert_all_mocked=False)
async def test_simultaneous_submissions_create_exactly_one_task(
    respx_mock, client, auth, db_session
):
    await make_node(db_session, "race-cli", role="ai_compute")
    release = asyncio.Event()

    async def held_response(request):
        await release.wait()
        return httpx.Response(200, json={"status": "succeeded", "output": "one execution"})

    route = respx_mock.post("http://race-cli:8010/execute").mock(side_effect=held_response)
    w = await workspace(client, auth)
    s = await session(client, auth, w["id"])
    url = f"/api/v1/cli/sessions/{s['id']}/tasks"
    requests = {
        asyncio.create_task(client.post(url, headers=auth, json={"prompt": prompt}))
        for prompt in ("First prompt", "Second prompt")
    }
    try:
        finished, pending = await asyncio.wait(
            requests, timeout=5, return_when=asyncio.FIRST_COMPLETED
        )
        assert len(finished) == 1 and len(pending) == 1
        assert next(iter(finished)).result().status_code == 409
    finally:
        release.set()
        responses = await asyncio.gather(*requests)
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert route.call_count == 1
    listed = await client.get(f"/api/v1/cli/workspaces/{w['id']}/sessions", headers=auth)
    assert len(listed.json()[0]["tasks"]) == 1
