"""Universal LLM layer API (ADR-031): providers, accounts, models, health,
routing, chat and usage. The legacy /providers and /admin/providers routes
are unchanged."""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbDep, Principal, require_roles
from app.llm import catalog, discovery, oauth, vault
from app.llm.accounts import (
    AccountError,
    Actor,
    can_manage,
    can_use,
    connection_for,
    create_account,
    delete_account,
    probe_account,
    require_account,
    update_account,
    usable_accounts,
    visible_accounts,
)
from app.llm.adapters.base import health_from_error
from app.llm.errors import LLMError
from app.llm.spec import AuthMethod
from app.models.llm import LLMProviderAccount
from app.models.user import ROLE_ADMIN, ROLE_OPERATOR
from app.schemas.llm import (
    AccountCreate,
    AccountOut,
    AccountProbeOut,
    AccountProbeRequest,
    AccountUpdate,
    HealthOut,
    ModelListOut,
    ModelOut,
    OAuthCompleteIn,
    OAuthStartIn,
    OAuthStartOut,
    ProviderOut,
    account_out,
)

router = APIRouter(prefix="/llm", tags=["llm"])

OperatorDep = Annotated[Principal, Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR))]


def _actor(principal: Principal) -> Actor:
    return Actor.from_principal(principal)


def _http(exc: AccountError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


async def _out(db: AsyncSession, account: LLMProviderAccount, actor: Actor) -> AccountOut:
    return account_out(
        account,
        mine=actor.user_id is not None and account.owner_user_id == actor.user_id,
        usable=can_use(account, actor),
        manageable=can_manage(account, actor),
        has_key=await vault.has_credential(db, account.id),
    )


# --- providers -------------------------------------------------------------------


def _provider_out(provider: str) -> ProviderOut:
    spec = catalog.spec(provider)
    return ProviderOut(
        id=spec.id,
        display_name=spec.display_name,
        kind=spec.kind.value,
        auth_methods=[m.value for m in spec.auth_methods],
        credential_required=spec.credential_required,
        base_url_mode=spec.base_url_mode.value,
        default_base_url=spec.default_base_url,
        official_base_urls=list(spec.official_base_urls),
        capabilities=spec.capabilities,
        docs_url=spec.docs_url,
        subscription_note=spec.subscription_note,
        notes=list(spec.notes),
    )


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(_principal: OperatorDep) -> list[ProviderOut]:
    """Every supported provider, its auth methods and API-level capabilities."""
    return [_provider_out(spec.id) for spec in catalog.specs()]


@router.get("/providers/{provider}", response_model=ProviderOut)
async def get_provider(provider: str, _principal: OperatorDep) -> ProviderOut:
    if not catalog.known(provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider")
    return _provider_out(provider)


# --- accounts ----------------------------------------------------------------------


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(principal: OperatorDep, db: DbDep) -> list[AccountOut]:
    """Deployment accounts plus your own. Administrators also see the
    metadata (never the credential) of other users' personal accounts."""
    actor = _actor(principal)
    return [await _out(db, a, actor) for a in await visible_accounts(db, actor)]


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def connect_account(body: AccountCreate, principal: OperatorDep, db: DbDep) -> AccountOut:
    """Connect a provider account. The API key is encrypted before it is
    stored and is never returned."""
    actor = _actor(principal)
    key = body.api_key.get_secret_value() if body.api_key else None
    try:
        account = await create_account(
            db,
            actor,
            provider=body.provider,
            label=body.label,
            scope=body.scope,
            base_url=body.base_url,
            api_key=key,
        )
    except AccountError as exc:
        raise _http(exc) from None
    finally:
        key = None
    await db.commit()
    await db.refresh(account)
    return await _out(db, account, actor)


@router.get("/accounts/{account_id}", response_model=AccountOut)
async def get_account(account_id: uuid.UUID, principal: OperatorDep, db: DbDep) -> AccountOut:
    actor = _actor(principal)
    try:
        account = await require_account(db, actor, account_id, "view")
    except AccountError as exc:
        raise _http(exc) from None
    return await _out(db, account, actor)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def patch_account(
    account_id: uuid.UUID, body: AccountUpdate, principal: OperatorDep, db: DbDep
) -> AccountOut:
    """Rename, disable/enable, change the endpoint or replace the API key."""
    actor = _actor(principal)
    key = body.api_key.get_secret_value() if body.api_key else None
    try:
        account = await require_account(db, actor, account_id, "manage")
        await update_account(
            db,
            actor,
            account,
            label=body.label,
            status=body.status,
            base_url=body.base_url,
            api_key=key,
        )
    except AccountError as exc:
        raise _http(exc) from None
    finally:
        key = None
    await db.commit()
    await db.refresh(account)
    discovery.invalidate(account.id)
    return await _out(db, account, actor)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_account(account_id: uuid.UUID, principal: OperatorDep, db: DbDep) -> None:
    """Disconnect: deletes the stored credential and removes the account from
    every routing chain. Revoke the key at the provider as well."""
    actor = _actor(principal)
    try:
        account = await require_account(db, actor, account_id, "remove")
        await delete_account(db, actor, account)
    except AccountError as exc:
        raise _http(exc) from None
    await db.commit()
    discovery.invalidate(account_id)


@router.post("/accounts/{account_id}/test", response_model=AccountProbeOut)
async def test_connection(
    account_id: uuid.UUID,
    principal: OperatorDep,
    db: DbDep,
    body: AccountProbeRequest | None = None,
) -> AccountProbeOut:
    """Real connectivity check: an authenticated model listing, or a
    minimal prompt to `model` when given (the provider may charge for it)."""
    actor = _actor(principal)
    try:
        account = await require_account(db, actor, account_id, "use")
    except AccountError as exc:
        raise _http(exc) from None
    outcome = await probe_account(db, actor, account, body.model if body else None)
    await db.commit()
    spec = catalog.spec(account.provider)
    return AccountProbeOut(
        status=outcome.status,
        detail=outcome.detail,
        latency_ms=outcome.latency_ms,
        models_available=outcome.models_available,
        api_access=account.api_access,
        subscription_note=spec.subscription_note if outcome.status == "unauthorized" else None,
    )


@router.get("/accounts/{account_id}/health", response_model=HealthOut)
async def account_health(account_id: uuid.UUID, principal: OperatorDep, db: DbDep) -> HealthOut:
    actor = _actor(principal)
    try:
        account = await require_account(db, actor, account_id, "use")
    except AccountError as exc:
        raise _http(exc) from None
    try:
        conn = await connection_for(db, account)
        health = await catalog.get(account.provider).health_check(conn)
    except LLMError as exc:
        health = health_from_error(exc)
    return HealthOut(account_id=account.id, provider=account.provider, **health.model_dump())


@router.get("/accounts/{account_id}/models", response_model=ModelListOut)
async def account_models(
    account_id: uuid.UUID,
    principal: OperatorDep,
    db: DbDep,
    refresh: bool = False,
) -> ModelListOut:
    """Models the provider reports for this account, with the capabilities it
    documents. Nothing is filled in when the provider does not say."""
    actor = _actor(principal)
    try:
        account = await require_account(db, actor, account_id, "use")
    except AccountError as exc:
        raise _http(exc) from None
    models = await discovery.models_for(db, account, refresh=refresh)
    return ModelListOut(models=[_model_out(account, m) for m in models])


def _model_out(account: LLMProviderAccount, model) -> ModelOut:
    adapter = catalog.get(account.provider)
    return ModelOut(
        ref=model.ref,
        provider=account.provider,
        id=model.id,
        account_id=account.id,
        account_label=account.label,
        display_name=model.display_name,
        context_window=model.context_window,
        max_output_tokens=model.max_output_tokens,
        capabilities=adapter.capabilities(model),
    )


@router.get("/models", response_model=ModelListOut)
async def all_models(
    principal: OperatorDep,
    db: DbDep,
    provider: Annotated[str | None, Query(max_length=40)] = None,
) -> ModelListOut:
    """Models across every account you can use. One slow or failing provider
    does not hide the others; its failure is listed in `errors`."""
    actor = _actor(principal)
    accounts = [
        a for a in await usable_accounts(db, actor) if provider is None or a.provider == provider
    ]
    models: list[ModelOut] = []
    failures: list[dict[str, str]] = []
    for account in accounts:  # sequential: the DB session is not concurrency-safe
        try:
            found = await asyncio.wait_for(discovery.models_for(db, account), timeout=20)
            models += [_model_out(account, m) for m in found]
        except (LLMError, TimeoutError) as exc:
            message = exc.public_message if isinstance(exc, LLMError) else "Timed out"
            failures.append({"account_id": str(account.id), "error": message})
    return ModelListOut(models=models, errors=failures)


# --- OpenRouter sign-in (OAuth PKCE) ---------------------------------------------


@router.post("/oauth/openrouter/start", response_model=OAuthStartOut)
async def openrouter_oauth_start(body: OAuthStartIn, principal: OperatorDep) -> OAuthStartOut:
    """Begin OpenRouter's official PKCE sign-in. Open `authorization_url` in a
    browser; OpenRouter redirects the code to `callback_url` (or shows it)."""
    if principal.type != "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign-in requires a user session")
    try:
        url, flow = oauth.start(principal.id, body.callback_url)
    except oauth.OAuthFlowError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    return OAuthStartOut(authorization_url=url, flow=flow, expires_in=oauth.FLOW_TTL_SECONDS)


@router.post(
    "/oauth/openrouter/complete", response_model=AccountOut, status_code=status.HTTP_201_CREATED
)
async def openrouter_oauth_complete(
    body: OAuthCompleteIn, principal: OperatorDep, db: DbDep
) -> AccountOut:
    """Exchange the code for an OpenRouter API key and store it as an account."""
    if principal.type != "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign-in requires a user session")
    actor = _actor(principal)
    if body.scope == "deployment" and not actor.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only administrators manage deployment accounts"
        )
    try:
        key = await oauth.exchange(body.flow, body.code, principal.id)
    except oauth.OAuthFlowError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    try:
        account = await create_account(
            db,
            actor,
            provider="openrouter",
            label=body.label,
            scope=body.scope,
            api_key=key,
            auth_method=AuthMethod.OAUTH_PKCE,
        )
    except AccountError as exc:
        raise _http(exc) from None
    finally:
        key = None
    await db.commit()
    await db.refresh(account)
    return await _out(db, account, actor)
