from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Role, User
from app.models.user import ALL_ROLES, ROLE_ADMIN, ROLE_NODE, ROLE_OPERATOR

ADMIN_EMAIL = "admin@test.local"
OPERATOR_EMAIL = "operator@test.local"
PASSWORD = "test-password-123"


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def sessionmaker_(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker_() as session:
        yield session


@pytest_asyncio.fixture
async def roles(db_session: AsyncSession) -> dict[str, Role]:
    out: dict[str, Role] = {}
    for name in ALL_ROLES:
        role = Role(name=name)
        db_session.add(role)
        out[name] = role
    await db_session.commit()
    return out


@pytest_asyncio.fixture
async def users(db_session: AsyncSession, roles: dict[str, Role]) -> dict[str, User]:
    password_hash = hash_password(PASSWORD)  # hash once; argon2 is deliberately slow
    admin = User(
        email=ADMIN_EMAIL, password_hash=password_hash, role_id=roles[ROLE_ADMIN].id, is_active=True
    )
    operator = User(
        email=OPERATOR_EMAIL,
        password_hash=password_hash,
        role_id=roles[ROLE_OPERATOR].id,
        is_active=True,
    )
    db_session.add_all([admin, operator])
    await db_session.commit()
    return {ROLE_ADMIN: admin, ROLE_OPERATOR: operator}


@pytest_asyncio.fixture
async def client(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with sessionmaker_() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> str:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


__all__ = ["ROLE_ADMIN", "ROLE_NODE", "ROLE_OPERATOR", "bearer", "login"]
