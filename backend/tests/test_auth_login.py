from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from tests.conftest import ADMIN_EMAIL, PASSWORD, bearer, login


async def test_login_success_returns_token(client: AsyncClient, users: dict) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["access_token"]


async def test_login_wrong_password_rejected(client: AsyncClient, users: dict) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"}
    )
    assert response.status_code == 401


async def test_login_unknown_user_rejected(client: AsyncClient, users: dict) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@test.local", "password": PASSWORD}
    )
    assert response.status_code == 401


async def test_login_inactive_user_rejected(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    user: User = users["admin"]
    user.is_active = False
    db_session.add(user)
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": PASSWORD}
    )
    assert response.status_code == 401


async def test_token_grants_access_to_me(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    response = await client.get("/api/v1/me", headers=bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "user"
    assert body["email"] == ADMIN_EMAIL
    assert body["role"] == "admin"


async def test_me_without_credentials_is_401(client: AsyncClient, users: dict) -> None:
    response = await client.get("/api/v1/me")
    assert response.status_code == 401


async def test_garbage_token_is_401(client: AsyncClient, users: dict) -> None:
    response = await client.get("/api/v1/me", headers=bearer("not-a-jwt"))
    assert response.status_code == 401
