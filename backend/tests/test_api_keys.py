from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import API_KEY_HEADER
from app.models import AuditLog
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login


async def _create_key(client: AsyncClient, token: str, name: str = "garage-box") -> dict:
    response = await client.post(
        "/api/v1/admin/api-keys", json={"name": name, "role": "node"}, headers=bearer(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_admin_creates_key_and_key_works(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    created = await _create_key(client, token)

    assert created["api_key"].startswith("lyc_")
    assert created["role"] == "node"

    me = await client.get("/api/v1/me", headers={API_KEY_HEADER: created["api_key"]})
    assert me.status_code == 200
    assert me.json()["role"] == "node"
    assert me.json()["name"] == "garage-box"


async def test_listing_never_exposes_secrets(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    created = await _create_key(client, token)

    listed = await client.get("/api/v1/admin/api-keys", headers=bearer(token))
    assert listed.status_code == 200
    entry = next(e for e in listed.json() if e["id"] == created["id"])
    assert "api_key" not in entry
    assert "key_hash" not in entry
    assert entry["key_prefix"] == created["key_prefix"]


async def test_revoked_key_stops_working(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    created = await _create_key(client, token)

    revoke = await client.delete(f"/api/v1/admin/api-keys/{created['id']}", headers=bearer(token))
    assert revoke.status_code == 204

    me = await client.get("/api/v1/me", headers={API_KEY_HEADER: created["api_key"]})
    assert me.status_code == 401


async def test_operator_cannot_manage_keys(client: AsyncClient, users: dict) -> None:
    token = await login(client, OPERATOR_EMAIL)
    response = await client.post(
        "/api/v1/admin/api-keys", json={"name": "x", "role": "node"}, headers=bearer(token)
    )
    assert response.status_code == 403


async def test_key_lifecycle_is_audited(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    token = await login(client, ADMIN_EMAIL)
    created = await _create_key(client, token)
    await client.delete(f"/api/v1/admin/api-keys/{created['id']}", headers=bearer(token))

    actions = [
        row[0]
        for row in await db_session.execute(
            select(AuditLog.action).where(AuditLog.resource_type == "api_key")
        )
    ]
    assert "apikey.create" in actions
    assert "apikey.revoke" in actions
