from httpx import AsyncClient

from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login


async def test_admin_can_read_audit_logs(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    response = await client.get("/api/v1/admin/audit-logs", headers=bearer(token))
    assert response.status_code == 200
    assert isinstance(response.json(), list)


async def test_operator_denied_on_admin_endpoint(client: AsyncClient, users: dict) -> None:
    token = await login(client, OPERATOR_EMAIL)
    response = await client.get("/api/v1/admin/audit-logs", headers=bearer(token))
    assert response.status_code == 403


async def test_anonymous_denied_on_admin_endpoint(client: AsyncClient, users: dict) -> None:
    response = await client.get("/api/v1/admin/audit-logs")
    assert response.status_code == 401
