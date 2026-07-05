import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import API_KEY_HEADER
from app.models import AuditLog
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login
from tests.test_nodes_register import payload


async def _register(client: AsyncClient, node_api_key: tuple, name: str = "node-a") -> dict:
    full_key, _ = node_api_key
    response = await client.post(
        "/api/v1/nodes/register", json=payload(name), headers={API_KEY_HEADER: full_key}
    )
    assert response.status_code == 201
    return response.json()


async def test_list_and_filter_by_status(
    client: AsyncClient, node_api_key: tuple, users: dict
) -> None:
    await _register(client, node_api_key)
    token = await login(client, OPERATOR_EMAIL)

    all_nodes = await client.get("/api/v1/nodes", headers=bearer(token))
    assert all_nodes.status_code == 200
    assert len(all_nodes.json()) == 1

    registered = await client.get("/api/v1/nodes?status=registered", headers=bearer(token))
    assert len(registered.json()) == 1

    online = await client.get("/api/v1/nodes?status=online", headers=bearer(token))
    assert online.json() == []


async def test_get_node_by_id(client: AsyncClient, node_api_key: tuple, users: dict) -> None:
    created = await _register(client, node_api_key)
    token = await login(client, OPERATOR_EMAIL)
    response = await client.get(f"/api/v1/nodes/{created['id']}", headers=bearer(token))
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["hardware_profile"]["os"]["name"] == "Ubuntu"


async def test_get_unknown_node_is_404_envelope(client: AsyncClient, users: dict) -> None:
    token = await login(client, OPERATOR_EMAIL)
    response = await client.get(f"/api/v1/nodes/{uuid.uuid4()}", headers=bearer(token))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_patch_role_persists_and_audits(
    client: AsyncClient, node_api_key: tuple, users: dict, db_session: AsyncSession
) -> None:
    created = await _register(client, node_api_key)
    token = await login(client, ADMIN_EMAIL)

    response = await client.patch(
        f"/api/v1/nodes/{created['id']}", json={"role": "ai_compute"}, headers=bearer(token)
    )
    assert response.status_code == 200
    assert response.json()["role"] == "ai_compute"

    fetched = await client.get(f"/api/v1/nodes/{created['id']}", headers=bearer(token))
    assert fetched.json()["role"] == "ai_compute"

    entry = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "node.update"))
    ).scalar_one()
    assert entry.actor_user_id == users["admin"].id
    assert entry.detail == {"role": "ai_compute"}


async def test_patch_invalid_role_rejected(
    client: AsyncClient, node_api_key: tuple, users: dict
) -> None:
    created = await _register(client, node_api_key)
    token = await login(client, ADMIN_EMAIL)
    response = await client.patch(
        f"/api/v1/nodes/{created['id']}", json={"role": "supercomputer"}, headers=bearer(token)
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_node_key_cannot_patch(client: AsyncClient, node_api_key: tuple) -> None:
    created = await _register(client, node_api_key)
    full_key, _ = node_api_key
    response = await client.patch(
        f"/api/v1/nodes/{created['id']}",
        json={"role": "hybrid"},
        headers={API_KEY_HEADER: full_key},
    )
    assert response.status_code == 403


async def test_list_unauthenticated_rejected(client: AsyncClient, roles: dict) -> None:
    response = await client.get("/api/v1/nodes")
    assert response.status_code == 401
