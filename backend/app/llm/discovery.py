"""Per-account model discovery with a short in-process cache.

The cache only saves provider round-trips; it is per worker and is never
the source of truth for whether a call is allowed.
"""

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm import catalog
from app.llm.accounts import connection_for
from app.llm.types import ModelInfo
from app.models.llm import LLMProviderAccount

_cache: dict[uuid.UUID, tuple[float, list[ModelInfo]]] = {}


async def models_for(
    db: AsyncSession, account: LLMProviderAccount, *, refresh: bool = False
) -> list[ModelInfo]:
    hit = _cache.get(account.id)
    if hit and not refresh and hit[0] > time.monotonic():
        return hit[1]
    conn = await connection_for(db, account)
    models = await catalog.get(account.provider).list_models(conn)
    ttl = get_settings().llm_models_cache_seconds
    if ttl:
        _cache[account.id] = (time.monotonic() + ttl, models)
    return models


def cached_model(account_id: uuid.UUID, model_id: str) -> ModelInfo | None:
    hit = _cache.get(account_id)
    if not hit or hit[0] <= time.monotonic():
        return None
    return next((m for m in hit[1] if m.id == model_id), None)


def invalidate(account_id: uuid.UUID | None = None) -> None:
    if account_id is None:
        _cache.clear()
    else:
        _cache.pop(account_id, None)
