from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import API_KEY_HEADER, generate_api_key
from app.models import ApiKey, Role
from tests.conftest import ROLE_NODE


async def _create_key(
    db_session: AsyncSession, roles: dict[str, Role], **overrides
) -> tuple[str, ApiKey]:
    full_key, prefix, key_hash = generate_api_key()
    record = ApiKey(
        key_prefix=prefix,
        key_hash=key_hash,
        name="test-node-key",
        role_id=roles[ROLE_NODE].id,
        **overrides,
    )
    db_session.add(record)
    await db_session.commit()
    return full_key, record


async def test_api_key_authenticates_on_me(
    client: AsyncClient, db_session: AsyncSession, roles: dict[str, Role]
) -> None:
    full_key, _ = await _create_key(db_session, roles)
    response = await client.get("/api/v1/me", headers={API_KEY_HEADER: full_key})
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "api_key"
    assert body["role"] == "node"
    assert body["name"] == "test-node-key"


async def test_api_key_updates_last_used(
    client: AsyncClient, db_session: AsyncSession, roles: dict[str, Role]
) -> None:
    full_key, record = await _create_key(db_session, roles)
    assert record.last_used_at is None
    await client.get("/api/v1/me", headers={API_KEY_HEADER: full_key})
    await db_session.refresh(record)
    assert record.last_used_at is not None


async def test_unknown_api_key_rejected(client: AsyncClient, roles: dict[str, Role]) -> None:
    response = await client.get(
        "/api/v1/me", headers={API_KEY_HEADER: "lyc_deadbeef_notarealsecret"}
    )
    assert response.status_code == 401


async def test_malformed_api_key_rejected(client: AsyncClient, roles: dict[str, Role]) -> None:
    response = await client.get("/api/v1/me", headers={API_KEY_HEADER: "garbage"})
    assert response.status_code == 401


async def test_revoked_api_key_rejected(
    client: AsyncClient, db_session: AsyncSession, roles: dict[str, Role]
) -> None:
    full_key, _ = await _create_key(db_session, roles, revoked_at=datetime.now(UTC))
    response = await client.get("/api/v1/me", headers={API_KEY_HEADER: full_key})
    assert response.status_code == 401


async def test_node_api_key_denied_on_admin_endpoint(
    client: AsyncClient, db_session: AsyncSession, roles: dict[str, Role]
) -> None:
    full_key, _ = await _create_key(db_session, roles)
    response = await client.get("/api/v1/admin/audit-logs", headers={API_KEY_HEADER: full_key})
    assert response.status_code == 403
