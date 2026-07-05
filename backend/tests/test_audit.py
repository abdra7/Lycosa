from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from tests.conftest import ADMIN_EMAIL, bearer, login


async def _actions(db_session: AsyncSession) -> list[str]:
    result = await db_session.execute(select(AuditLog.action))
    return [row[0] for row in result]


async def test_login_success_writes_audit_row(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    await login(client, ADMIN_EMAIL)
    assert "auth.login.success" in await _actions(db_session)


async def test_login_failure_writes_audit_row(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
    assert "auth.login.failure" in await _actions(db_session)


async def test_logout_writes_audit_row(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    token = await login(client, ADMIN_EMAIL)
    await client.post("/api/v1/auth/logout", headers=bearer(token))
    assert "auth.logout" in await _actions(db_session)


async def test_audit_row_records_actor(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    await login(client, ADMIN_EMAIL)
    entry = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "auth.login.success"))
    ).scalar_one()
    assert entry.actor_user_id == users["admin"].id
    assert entry.resource_type == "session"


async def test_audit_logs_visible_via_admin_endpoint(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    # a failed login to generate a second distinct entry
    await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "x"})

    response = await client.get("/api/v1/admin/audit-logs", headers=bearer(token))
    assert response.status_code == 200
    actions = {e["action"] for e in response.json()}
    assert {"auth.login.success", "auth.login.failure"} <= actions
