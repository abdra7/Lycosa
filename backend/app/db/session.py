from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Per-request database session dependency."""
    async with get_runtime_sessionmaker()() as session:
        yield session


# Seam for code that needs sessions outside the request scope (parallel
# workflow branches, background sweeps). Tests install their own factory.
_sessionmaker_override: async_sessionmaker[AsyncSession] | None = None


def get_runtime_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return _sessionmaker_override if _sessionmaker_override is not None else get_sessionmaker()


def set_sessionmaker_override(factory: async_sessionmaker[AsyncSession] | None) -> None:
    global _sessionmaker_override
    _sessionmaker_override = factory
