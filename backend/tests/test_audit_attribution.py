"""Security audit 2026-10 (ADR-030): audit rows attribute API-key actors to
actor_api_key_id (never the users FK), and a credential change cannot outrun
its audit row."""

import pytest
import pytest_asyncio
import respx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.admin as admin_api
from app.core.security import API_KEY_HEADER, generate_api_key
from app.models import ApiKey, AuditLog
from app.models.user import ROLE_ADMIN, ROLE_OPERATOR
from app.services import provider_secrets
from tests.conftest import ADMIN_EMAIL, login, make_node
from tests.test_knowledge_ingest import create_collection
from tests.test_workflow_run import agent_reply, create_workflow, run_workflow


async def _key(db_session: AsyncSession, roles: dict, role: str) -> tuple[dict, ApiKey]:
    full_key, prefix, key_hash = generate_api_key()
    record = ApiKey(
        key_prefix=prefix, key_hash=key_hash, name=f"{role}-key", role_id=roles[role].id
    )
    db_session.add(record)
    await db_session.commit()
    return {API_KEY_HEADER: full_key}, record


@pytest_asyncio.fixture
async def admin_key(db_session, roles):
    return await _key(db_session, roles, ROLE_ADMIN)


@pytest_asyncio.fixture
async def operator_key(db_session, roles):
    return await _key(db_session, roles, ROLE_OPERATOR)


@pytest.fixture(autouse=True)
def _isolated_session_keys(monkeypatch):
    monkeypatch.setattr(provider_secrets, "_session_keys", {})


async def _row(db_session: AsyncSession, action: str) -> AuditLog:
    return (
        await db_session.execute(select(AuditLog).where(AuditLog.action == action))
    ).scalar_one()


async def test_admin_key_credential_change_is_attributed_to_the_key(
    client: AsyncClient, db_session: AsyncSession, users: dict, admin_key
) -> None:
    headers, record = admin_key
    route = "/api/v1/admin/providers/openrouter/credential"
    put = await client.put(route, json={"key": "dummy-key", "storage": "session"}, headers=headers)
    assert put.status_code == 204, put.text
    deleted = await client.delete(f"{route}?storage=session", headers=headers)
    assert deleted.status_code == 204

    for action in ("provider.credential.set", "provider.credential.delete"):
        row = await _row(db_session, action)
        assert row.actor_api_key_id == record.id
        assert row.actor_user_id is None


async def test_credential_is_not_written_when_audit_fails(
    client: AsyncClient, users: dict, admin_key, monkeypatch
) -> None:
    async def broken_audit(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(admin_api, "audit", broken_audit)
    headers, _ = admin_key
    with pytest.raises(RuntimeError):
        await client.put(
            "/api/v1/admin/providers/openrouter/credential",
            json={"key": "dummy-key", "storage": "session"},
            headers=headers,
        )
    assert "openrouter" not in provider_secrets._session_keys


async def test_admin_key_mint_and_revoke_are_attributed_to_the_key(
    client: AsyncClient, db_session: AsyncSession, users: dict, admin_key, node_api_key
) -> None:
    headers, record = admin_key
    minted = await client.post(
        "/api/v1/admin/api-keys", json={"name": "minted", "role": "node"}, headers=headers
    )
    assert minted.status_code == 201
    revoked = await client.delete(f"/api/v1/admin/api-keys/{node_api_key[1].id}", headers=headers)
    assert revoked.status_code == 204

    for action in ("apikey.create", "apikey.revoke"):
        row = await _row(db_session, action)
        assert row.actor_api_key_id == record.id
        assert row.actor_user_id is None


async def test_operator_key_mutations_record_the_key(
    client: AsyncClient, db_session: AsyncSession, users: dict, qdrant, operator_key
) -> None:
    headers, record = operator_key
    collection = (
        await client.post("/api/v1/knowledge/collections", json={"name": "c1"}, headers=headers)
    ).json()
    await client.delete(f"/api/v1/knowledge/collections/{collection['id']}", headers=headers)
    node = await make_node(db_session, "n1")
    await client.patch(f"/api/v1/nodes/{node.id}", json={"name": "renamed"}, headers=headers)
    await client.post(
        "/api/v1/workflows",
        json={"name": "wf", "definition": {"steps": [{"id": "a", "kind": "approval"}]}},
        headers=headers,
    )

    for action in (
        "knowledge.collection.create",
        "knowledge.collection.delete",
        "node.update",
        "workflow.create",
    ):
        assert (await _row(db_session, action)).actor_api_key_id == record.id, action


async def test_admin_key_node_delete_records_the_key(
    client: AsyncClient, db_session: AsyncSession, users: dict, admin_key
) -> None:
    headers, record = admin_key
    node = await make_node(db_session, "doomed")
    assert (await client.delete(f"/api/v1/nodes/{node.id}", headers=headers)).status_code == 204
    assert (await _row(db_session, "node.delete")).actor_api_key_id == record.id


@respx.mock(assert_all_mocked=False)
async def test_api_key_approval_records_actor_and_ip(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict, operator_key
) -> None:
    headers, record = operator_key
    await make_node(db_session, "worker", role="hybrid")
    respx_mock.post("http://worker:8010/execute").mock(side_effect=[agent_reply("after-done")])
    token = await login(client, ADMIN_EMAIL)
    workflow = await create_workflow(
        client,
        token,
        "gated",
        [{"id": "gate", "kind": "approval"}, {"id": "after", "kind": "task", "prompt": "x"}],
    )
    run = await run_workflow(client, token, workflow["id"], "in")
    assert run["status"] == "paused"

    approved = await client.post(
        f"/api/v1/workflows/{workflow['id']}/runs/{run['id']}/approve",
        json={"approved": True},
        headers=headers,
    )
    assert approved.status_code == 200 and approved.json()["status"] == "succeeded"

    row = await _row(db_session, "workflow.run.approved")
    assert row.actor_api_key_id == record.id
    assert row.ip_address == "127.0.0.1"
    resumed = await _row(db_session, "task.submit")
    assert resumed.actor_api_key_id == record.id


async def test_create_collection_fixture_still_attributes_users(
    client: AsyncClient, db_session: AsyncSession, users: dict, qdrant
) -> None:
    token = await login(client, ADMIN_EMAIL)
    await create_collection(client, token, name="by-user")
    row = await _row(db_session, "knowledge.collection.create")
    assert row.actor_user_id == users["admin"].id
    assert row.actor_api_key_id is None
