"""Provider accounts: who may see, use and change them, and how a call
reaches one.

- deployment account (owner NULL): admin-managed; every admin/operator may
  use it (users and operator API keys alike)
- personal account: only its owner may use, test or change it; admins may
  see its metadata and remove it, but never use it or replace its key, so
  no request ever runs on someone else's credential
"""

import ipaddress
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm import catalog, vault
from app.llm.adapters.base import health_from_error
from app.llm.errors import (
    AuthenticationError,
    EndpointNotAllowedError,
    LLMError,
    PermissionDeniedError,
    QuotaExceededError,
)
from app.llm.http import Connection
from app.llm.netpolicy import (
    PUBLIC_ONLY,
    AddressPolicy,
    Resolver,
    local_policy,
    parse_networks,
    private_only_policy,
    resolve_checked,
    system_resolver,
    validate_base_url,
)
from app.llm.spec import AuthMethod, BaseUrlMode, ProviderKind, ProviderSpec
from app.llm.types import HealthStatus, LLMRequest, Message
from app.models.llm import LLMProviderAccount, LLMRoutingPolicy
from app.models.user import ROLE_ADMIN
from app.services.audit import audit

MAX_ACCOUNTS_PER_OWNER = 50
Scope = Literal["personal", "deployment"]
SCOPE_DEPLOYMENT: Literal["deployment"] = "deployment"
SCOPE_PERSONAL: Literal["personal"] = "personal"
Need = Literal["view", "use", "manage", "remove"]

# overridable in tests; production resolves through the system resolver
RESOLVER: Resolver = system_resolver


class AccountError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class Actor:
    """The caller, reduced to what account rules need."""

    role: str
    user_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @classmethod
    def from_principal(cls, principal: Any) -> "Actor":
        return cls(
            role=principal.role,
            user_id=principal.id if principal.type == "user" else None,
            api_key_id=principal.id if principal.type == "api_key" else None,
        )


# --- rules --------------------------------------------------------------------


def is_deployment(account: LLMProviderAccount) -> bool:
    return account.owner_user_id is None


def scope_of(account: LLMProviderAccount) -> Scope:
    return SCOPE_DEPLOYMENT if is_deployment(account) else SCOPE_PERSONAL


def _owns(account: LLMProviderAccount, actor: Actor) -> bool:
    return actor.user_id is not None and account.owner_user_id == actor.user_id


def can_view(account: LLMProviderAccount, actor: Actor) -> bool:
    return is_deployment(account) or _owns(account, actor) or actor.is_admin


def can_use(account: LLMProviderAccount, actor: Actor) -> bool:
    return account.status == "active" and (is_deployment(account) or _owns(account, actor))


def can_manage(account: LLMProviderAccount, actor: Actor) -> bool:
    return (is_deployment(account) and actor.is_admin) or _owns(account, actor)


def can_remove(account: LLMProviderAccount, actor: Actor) -> bool:
    return can_manage(account, actor) or actor.is_admin


_CHECKS = {"view": can_view, "use": can_use, "manage": can_manage, "remove": can_remove}


def _trusted_network_owner(account: LLMProviderAccount) -> bool:
    owner = account.owner
    return is_deployment(account) or (owner is not None and owner.role.name == ROLE_ADMIN)


def address_policy(account: LLMProviderAccount, *, private_only: bool = False) -> AddressPolicy:
    """Where this account's calls may connect. Cloud providers: public
    addresses only. Local/compatible endpoints: the private networks the
    administrator allowed for this kind of owner."""
    spec = catalog.spec(account.provider)
    if spec.kind in (ProviderKind.CLOUD, ProviderKind.AGGREGATOR):
        return AddressPolicy(public_ok=False, label="none") if private_only else PUBLIC_ONLY
    settings = get_settings()
    networks = parse_networks(
        settings.llm_local_networks
        if _trusted_network_owner(account)
        else settings.llm_user_endpoint_networks
    )
    return private_only_policy(networks) if private_only else local_policy(networks)


def resolve_base_url(spec: ProviderSpec, requested: str | None) -> str:
    if spec.base_url_mode == BaseUrlMode.FIXED:
        if requested and requested.rstrip("/") != spec.default_base_url:
            raise EndpointNotAllowedError(f"{spec.display_name} only uses its official endpoint")
        return spec.default_base_url
    if spec.base_url_mode == BaseUrlMode.OFFICIAL_CHOICE:
        url = validate_base_url(requested or spec.default_base_url, allow_http=False)
        if not spec.accepts_official_base_url(url):
            raise EndpointNotAllowedError(f"Choose one of {spec.display_name}'s official endpoints")
        return url
    if not (requested or spec.default_base_url):
        raise EndpointNotAllowedError("This provider needs an endpoint URL")
    return validate_base_url(requested or spec.default_base_url, allow_http=spec.allow_http)


async def _locality(spec: ProviderSpec, url: str, policy: AddressPolicy) -> bool:
    """Check a user-supplied endpoint now (clear errors at connect time) and
    report whether every address is private (a 'local' account)."""
    if spec.kind in (ProviderKind.CLOUD, ProviderKind.AGGREGATOR):
        return False  # fixed official endpoints; enforced again per connection
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = await resolve_checked(parts.hostname or "", port, policy, RESOLVER)
    return all(not ipaddress.ip_address(a.split("%", 1)[0]).is_global for a in addresses)


def _audit_detail(account: LLMProviderAccount, **extra: Any) -> dict[str, Any]:
    return {
        "provider": account.provider,
        "label": account.label,
        "scope": scope_of(account),
        "host": urlsplit(account.base_url).hostname,
        **extra,
    }


# --- queries ------------------------------------------------------------------


async def get_account(db: AsyncSession, account_id: uuid.UUID) -> LLMProviderAccount | None:
    return (
        await db.execute(select(LLMProviderAccount).where(LLMProviderAccount.id == account_id))
    ).scalar_one_or_none()


async def require_account(
    db: AsyncSession, actor: Actor, account_id: uuid.UUID, need: Need
) -> LLMProviderAccount:
    account = await get_account(db, account_id)
    if account is None or not can_view(account, actor):
        raise AccountError(404, "Account not found")  # never confirm others' accounts exist
    if not _CHECKS[need](account, actor):
        if need == "use" and account.status != "active":
            raise AccountError(409, "Account is disabled")
        raise AccountError(403, "Not permitted for this account")
    return account


async def visible_accounts(db: AsyncSession, actor: Actor) -> list[LLMProviderAccount]:
    query = select(LLMProviderAccount).order_by(
        LLMProviderAccount.provider, LLMProviderAccount.label
    )
    if not actor.is_admin:
        query = query.where(
            or_(
                LLMProviderAccount.owner_user_id.is_(None),
                LLMProviderAccount.owner_user_id == actor.user_id,
            )
        )
    return list((await db.execute(query)).scalars())


async def usable_accounts(db: AsyncSession, actor: Actor) -> list[LLMProviderAccount]:
    return [a for a in await visible_accounts(db, actor) if can_use(a, actor)]


# --- mutations ---------------------------------------------------------------


async def create_account(
    db: AsyncSession,
    actor: Actor,
    *,
    provider: str,
    label: str,
    scope: Scope,
    base_url: str | None = None,
    api_key: str | None = None,
    auth_method: AuthMethod | None = None,
) -> LLMProviderAccount:
    if not catalog.known(provider):
        raise AccountError(422, "Unknown provider")
    spec = catalog.spec(provider)
    if scope == SCOPE_DEPLOYMENT and not actor.is_admin:
        raise AccountError(403, "Only administrators manage deployment accounts")
    if scope == SCOPE_PERSONAL and actor.user_id is None:
        raise AccountError(403, "API-key callers cannot own personal accounts")
    method = auth_method or (AuthMethod.API_KEY if api_key else AuthMethod.NONE)
    if method not in spec.auth_methods:
        raise AccountError(
            422, f"{spec.display_name} does not support {method.value} authentication"
        )
    if method == AuthMethod.NONE and api_key:
        raise AccountError(422, "An API key was supplied for an unauthenticated account")
    if method != AuthMethod.NONE:
        if not api_key:
            raise AccountError(422, f"{spec.display_name} needs an API key")
        try:
            vault.validate_secret(api_key)
        except ValueError:
            raise AccountError(422, "Invalid API key format") from None

    owner = actor.user_id if scope == SCOPE_PERSONAL else None
    existing = (
        (
            await db.execute(
                select(LLMProviderAccount).where(
                    LLMProviderAccount.owner_user_id.is_(None)
                    if owner is None
                    else LLMProviderAccount.owner_user_id == owner
                )
            )
        )
        .scalars()
        .all()
    )
    if len(existing) >= MAX_ACCOUNTS_PER_OWNER:
        raise AccountError(409, "Account limit reached")
    if any(a.provider == provider and a.label == label for a in existing):
        raise AccountError(409, "An account with this provider and label already exists")

    url = resolve_base_url(spec, base_url)
    account = LLMProviderAccount(
        owner_user_id=owner,
        provider=provider,
        label=label,
        auth_method=method.value,
        base_url=url,
        status="active",
        api_access="unknown",
        created_by_user_id=actor.user_id,
    )
    if owner is not None:
        from app.models import User

        account.owner = await db.get(User, owner)
    account.is_local = await _locality(spec, url, address_policy(account))
    if api_key and url.startswith("http://") and not account.is_local:
        raise AccountError(422, "API keys need HTTPS unless the endpoint is on a local network")
    db.add(account)
    await db.flush()
    # audit before the secret write (ADR-030): no unaudited credential change
    await audit(
        db,
        action="llm.account.create",
        actor_user_id=actor.user_id,
        actor_api_key_id=actor.api_key_id,
        resource_type="llm_account",
        resource_id=str(account.id),
        detail=_audit_detail(account, auth_method=method.value),
    )
    await db.flush()
    if api_key:
        await vault.store_credential(db, account, api_key)
    return account


async def update_account(
    db: AsyncSession,
    actor: Actor,
    account: LLMProviderAccount,
    *,
    label: str | None = None,
    status: Literal["active", "disabled"] | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> LLMProviderAccount:
    if not can_manage(account, actor):
        raise AccountError(403, "Not permitted for this account")
    spec = catalog.spec(account.provider)
    changed: list[str] = []
    if label is not None and label != account.label:
        clash = (
            await db.execute(
                select(LLMProviderAccount.id).where(
                    LLMProviderAccount.provider == account.provider,
                    LLMProviderAccount.label == label,
                    LLMProviderAccount.id != account.id,
                    LLMProviderAccount.owner_user_id.is_(None)
                    if account.owner_user_id is None
                    else LLMProviderAccount.owner_user_id == account.owner_user_id,
                )
            )
        ).first()
        if clash:
            raise AccountError(409, "An account with this provider and label already exists")
        account.label = label
        changed.append("label")
    if status is not None and status != account.status:
        account.status = status
        changed.append("status")
    if base_url is not None:
        url = resolve_base_url(spec, base_url)
        if url != account.base_url:
            account.is_local = await _locality(spec, url, address_policy(account))
            account.base_url = url
            account.api_access = "unknown"
            changed.append("base_url")
    if api_key is not None:
        if account.auth_method == AuthMethod.NONE.value:
            raise AccountError(422, "This account does not use an API key")
        try:
            vault.validate_secret(api_key)
        except ValueError:
            raise AccountError(422, "Invalid API key format") from None
        account.api_access = "unknown"
        changed.append("credential")
    if (
        account.base_url.startswith("http://")
        and not account.is_local
        and (api_key is not None or await vault.has_credential(db, account.id))
    ):
        raise AccountError(422, "API keys need HTTPS unless the endpoint is on a local network")
    if not changed:
        return account
    await audit(
        db,
        action="llm.account.update",
        actor_user_id=actor.user_id,
        actor_api_key_id=actor.api_key_id,
        resource_type="llm_account",
        resource_id=str(account.id),
        detail=_audit_detail(account, changed=changed),
    )
    await db.flush()
    if api_key is not None:
        await vault.store_credential(db, account, api_key)
    return account


async def delete_account(db: AsyncSession, actor: Actor, account: LLMProviderAccount) -> None:
    if not can_remove(account, actor):
        raise AccountError(403, "Not permitted for this account")
    await audit(
        db,
        action="llm.account.delete",
        actor_user_id=actor.user_id,
        actor_api_key_id=actor.api_key_id,
        resource_type="llm_account",
        resource_id=str(account.id),
        detail=_audit_detail(account),
    )
    await db.flush()
    await vault.delete_credential(db, account)
    # drop the account from every routing chain that referenced it
    target = str(account.id)
    for policy in (await db.execute(select(LLMRoutingPolicy))).scalars():
        chain = [e for e in policy.chain if e.get("account_id") != target]
        if chain != policy.chain:
            if chain:
                policy.chain = chain
            else:
                await db.delete(policy)
    await db.delete(account)
    await db.flush()


# --- calling an account ---------------------------------------------------------


async def connection_for(
    db: AsyncSession, account: LLMProviderAccount, *, private_only: bool = False
) -> Connection:
    credential = await vault.load_credential(db, account)
    return Connection(
        provider=account.provider,
        base_url=account.base_url,
        credential=credential,
        policy=address_policy(account, private_only=private_only),
        timeout=float(get_settings().llm_request_timeout_seconds),
    )


TEST_PROMPT = "Reply with the single word OK."


@dataclass
class ProbeOutcome:
    status: str  # ready | offline | unauthorized | error | unknown
    detail: str
    latency_ms: int | None = None
    models_available: int | None = None


async def probe_account(
    db: AsyncSession, actor: Actor, account: LLMProviderAccount, model: str | None = None
) -> ProbeOutcome:
    """A real call: model listing, or a minimal prompt when a model is given
    (which may incur a tiny provider charge). Updates the account status."""
    adapter = catalog.get(account.provider)
    started = time.monotonic()
    try:
        conn = await connection_for(db, account)
        if model:
            await adapter.generate(
                conn,
                LLMRequest(
                    model=model,
                    messages=[Message(role="user", content=TEST_PROMPT)],
                    max_tokens=16,
                ),
            )
            health = HealthStatus(
                status="ready", latency_ms=int((time.monotonic() - started) * 1000)
            )
        else:
            health = await adapter.health_check(conn)
    except (AuthenticationError, PermissionDeniedError, QuotaExceededError) as exc:
        health = HealthStatus(status="unauthorized", detail=exc.public_message)
    except LLMError as exc:
        health = health_from_error(exc)

    account.last_tested_at = datetime.now(UTC)
    account.last_test_status = health.status
    account.last_test_detail = (health.detail or "")[:200] or None
    if health.status == "ready":
        account.api_access = "available"
    elif health.status == "unauthorized":
        account.api_access = "unavailable"
    await audit(
        db,
        action="llm.account.test",
        actor_user_id=actor.user_id,
        actor_api_key_id=actor.api_key_id,
        resource_type="llm_account",
        resource_id=str(account.id),
        detail=_audit_detail(account, result=health.status, with_model=bool(model)),
    )
    await db.flush()
    return ProbeOutcome(
        status=health.status,
        detail=health.detail,
        latency_ms=health.latency_ms,
        models_available=health.models_available,
    )
