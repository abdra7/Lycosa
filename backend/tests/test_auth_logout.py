from httpx import AsyncClient

from tests.conftest import ADMIN_EMAIL, bearer, login


async def test_logout_revokes_session(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)

    # token works before logout
    assert (await client.get("/api/v1/me", headers=bearer(token))).status_code == 200

    response = await client.post("/api/v1/auth/logout", headers=bearer(token))
    assert response.status_code == 204

    # same token is now rejected server-side, even though the JWT hasn't expired
    assert (await client.get("/api/v1/me", headers=bearer(token))).status_code == 401


async def test_logout_without_token_is_401(client: AsyncClient, users: dict) -> None:
    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 401
