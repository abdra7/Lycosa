"""Purpose-based routing policies: an ordered (account, model) chain per
purpose; the first entry is the primary, the rest are fallbacks.

Resolution order for a purpose: the caller's own policy, then the
deployment policy, then (for non-local purposes) the 'default' purpose the
same way. 'private' and 'offline' only ever resolve to local accounts.

A deployment policy may only reference deployment accounts, so a shared
default can never route one user's request through another user's key.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import catalog
from app.llm.accounts import (
    SCOPE_DEPLOYMENT,
    AccountError,
    Actor,
    can_use,
    get_account,
    is_deployment,
)
from app.llm.errors import NoRouteError
from app.llm.spec import ProviderKind
from app.models.llm import LLMProviderAccount, LLMRoutingPolicy
from app.models.task import TaskType
from app.services.audit import audit

PURPOSES = ("default", "coding", "reasoning", "vision", "cheap", "private", "offline")
LOCAL_ONLY = frozenset({"private", "offline"})
MAX_CHAIN = 5

_TASK_PURPOSE = {TaskType.CODING: "coding", TaskType.VISION: "vision"}


def purpose_for_task(task_type: TaskType) -> str:
    return _TASK_PURPOSE.get(task_type, "default")


@dataclass(frozen=True)
class Target:
    account: LLMProviderAccount
    model: str


def is_local_target(account: LLMProviderAccount) -> bool:
    kind = catalog.spec(account.provider).kind
    return account.is_local and kind in (ProviderKind.LOCAL, ProviderKind.COMPATIBLE)


async def get_policy(
    db: AsyncSession, owner_user_id: uuid.UUID | None, purpose: str
) -> LLMRoutingPolicy | None:
    owner = (
        LLMRoutingPolicy.owner_user_id.is_(None)
        if owner_user_id is None
        else LLMRoutingPolicy.owner_user_id == owner_user_id
    )
    return (
        await db.execute(select(LLMRoutingPolicy).where(owner, LLMRoutingPolicy.purpose == purpose))
    ).scalar_one_or_none()


async def policies_for(db: AsyncSession, owner_user_id: uuid.UUID | None) -> list[LLMRoutingPolicy]:
    owner = (
        LLMRoutingPolicy.owner_user_id.is_(None)
        if owner_user_id is None
        else LLMRoutingPolicy.owner_user_id == owner_user_id
    )
    rows = (await db.execute(select(LLMRoutingPolicy).where(owner))).scalars().all()
    return sorted(rows, key=lambda p: PURPOSES.index(p.purpose) if p.purpose in PURPOSES else 99)


async def set_policy(
    db: AsyncSession,
    actor: Actor,
    *,
    scope: Literal["personal", "deployment"],
    purpose: str,
    chain: list[tuple[uuid.UUID, str]],
) -> LLMRoutingPolicy:
    if purpose not in PURPOSES:
        raise AccountError(422, "Unknown purpose")
    if not 1 <= len(chain) <= MAX_CHAIN:
        raise AccountError(422, f"A route needs 1 to {MAX_CHAIN} entries")
    if scope == SCOPE_DEPLOYMENT and not actor.is_admin:
        raise AccountError(403, "Only administrators manage deployment routing")
    if scope != SCOPE_DEPLOYMENT and actor.user_id is None:
        raise AccountError(403, "API-key callers have no personal routing")
    for account_id, _model in chain:
        account = await get_account(db, account_id)
        if account is None or not can_use(account, actor):
            raise AccountError(422, "Every route entry must be an active account you can use")
        if scope == SCOPE_DEPLOYMENT and not is_deployment(account):
            raise AccountError(422, "Deployment routes may only use deployment accounts")
        if purpose in LOCAL_ONLY and not is_local_target(account):
            raise AccountError(422, f"The '{purpose}' route may only use local runtimes")
    owner = None if scope == SCOPE_DEPLOYMENT else actor.user_id
    policy = await get_policy(db, owner, purpose) or LLMRoutingPolicy(
        owner_user_id=owner, purpose=purpose, chain=[]
    )
    policy.chain = [{"account_id": str(a), "model": m} for a, m in chain]
    db.add(policy)
    await audit(
        db,
        action="llm.routing.update",
        actor_user_id=actor.user_id,
        actor_api_key_id=actor.api_key_id,
        resource_type="llm_routing",
        resource_id=purpose,
        detail={"scope": scope, "entries": len(chain)},
    )
    await db.flush()
    return policy


async def delete_policy(
    db: AsyncSession, actor: Actor, *, scope: Literal["personal", "deployment"], purpose: str
) -> bool:
    if scope == SCOPE_DEPLOYMENT and not actor.is_admin:
        raise AccountError(403, "Only administrators manage deployment routing")
    owner = None if scope == SCOPE_DEPLOYMENT else actor.user_id
    if scope != SCOPE_DEPLOYMENT and owner is None:
        raise AccountError(403, "API-key callers have no personal routing")
    policy = await get_policy(db, owner, purpose)
    if policy is None:
        return False
    await audit(
        db,
        action="llm.routing.delete",
        actor_user_id=actor.user_id,
        actor_api_key_id=actor.api_key_id,
        resource_type="llm_routing",
        resource_id=purpose,
        detail={"scope": scope},
    )
    await db.delete(policy)
    await db.flush()
    return True


async def _chain_targets(
    db: AsyncSession, actor: Actor, policy: LLMRoutingPolicy, local_only: bool
) -> list[Target]:
    targets = []
    for entry in policy.chain[:MAX_CHAIN]:
        try:
            account = await get_account(db, uuid.UUID(str(entry.get("account_id"))))
        except ValueError:
            continue
        model = entry.get("model")
        if account is None or not isinstance(model, str) or not can_use(account, actor):
            continue  # removed, disabled, or not this caller's to use
        if local_only and not is_local_target(account):
            continue
        targets.append(Target(account, model))
    return targets


async def resolve(
    db: AsyncSession, actor: Actor, purpose: str, *, requires_privacy: bool = False
) -> list[Target]:
    """The usable route for a purpose, primary first. Raises NoRouteError."""
    if purpose not in PURPOSES:
        raise NoRouteError("Unknown routing purpose")
    local_only = requires_privacy or purpose in LOCAL_ONLY
    candidates = [purpose] if purpose == "default" else [purpose, "default"]
    for name in candidates:
        for owner in ([actor.user_id] if actor.user_id else []) + [None]:
            policy = await get_policy(db, owner, name)
            if policy is None:
                continue
            targets = await _chain_targets(db, actor, policy, local_only)
            if targets:
                return targets
    if local_only:
        raise NoRouteError("No local model route is configured for private/offline work")
    raise NoRouteError(f"No usable route is configured for '{purpose}'")
