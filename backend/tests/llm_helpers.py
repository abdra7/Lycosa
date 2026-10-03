"""Shared helpers for the universal LLM layer tests."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import catalog, vault
from app.models import LLMProviderAccount, LLMRoutingPolicy

TEST_KEY = "sk-test-gateway-secret-0001"


async def make_account(
    db: AsyncSession,
    provider: str = "openai",
    *,
    owner: uuid.UUID | None = None,
    label: str | None = None,
    base_url: str | None = None,
    key: str | None = TEST_KEY,
    is_local: bool = False,
    status: str = "active",
) -> LLMProviderAccount:
    spec = catalog.spec(provider)
    account = LLMProviderAccount(
        owner_user_id=owner,
        provider=provider,
        label=label or f"{provider}-{uuid.uuid4().hex[:6]}",
        auth_method="api_key" if key else "none",
        base_url=base_url or spec.default_base_url,
        is_local=is_local,
        status=status,
        api_access="unknown",
    )
    db.add(account)
    await db.flush()
    if key:
        await vault.store_credential(db, account, key)
    await db.commit()
    await db.refresh(account)
    return account


async def make_route(
    db: AsyncSession,
    purpose: str,
    entries: list[tuple[LLMProviderAccount, str]],
    owner: uuid.UUID | None = None,
) -> LLMRoutingPolicy:
    policy = LLMRoutingPolicy(
        owner_user_id=owner,
        purpose=purpose,
        chain=[{"account_id": str(a.id), "model": m} for a, m in entries],
    )
    db.add(policy)
    await db.commit()
    return policy


def openai_completion(content: str = "answer", finish: str = "stop", **usage) -> dict:
    return {
        "id": "r",
        "choices": [{"index": 0, "message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, **usage},
    }


def anthropic_message(text: str = "claude answer", stop: str = "end_turn") -> dict:
    return {
        "id": "m",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": {"input_tokens": 4, "output_tokens": 5},
    }
