"""Universal LLM layer API (ADR-031): providers, accounts, models, health,
routing, chat and usage. The legacy /providers and /admin/providers routes
are unchanged."""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbDep, Principal, require_roles
from app.db.session import get_runtime_sessionmaker
from app.llm import catalog, discovery, gateway, oauth, routing, vault
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
from app.llm.types import LLMRequest, Message
from app.models.llm import LLMProviderAccount, LLMRoutingPolicy, LLMUsage
from app.models.user import ROLE_ADMIN, ROLE_OPERATOR
from app.schemas.llm import (
    AccountCreate,
    AccountOut,
    AccountProbeOut,
    AccountProbeRequest,
    AccountUpdate,
    ChatRequest,
    ChatResponse,
    HealthOut,
    LLMTarget,
    ModelListOut,
    ModelOut,
    OAuthCompleteIn,
    OAuthStartIn,
    OAuthStartOut,
    PromptCheckRequest,
    ProviderOut,
    RouteEntry,
    RoutingOverviewOut,
    RoutingPolicyIn,
    RoutingPolicyOut,
    UsageOut,
    account_out,
)

logger = logging.getLogger("lycosa.llm")

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
        raise HTTPException(422, str(exc)) from None
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
        raise HTTPException(422, str(exc)) from None
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


# --- routing (default model, purposes, fallback chains) ----------------------------


def _policy_out(policy: LLMRoutingPolicy) -> RoutingPolicyOut:
    return RoutingPolicyOut(
        purpose=policy.purpose,
        scope="deployment" if policy.owner_user_id is None else "personal",
        chain=[RouteEntry(**entry) for entry in policy.chain],
        updated_at=policy.updated_at,
    )


@router.get("/routing", response_model=RoutingOverviewOut)
async def get_routing(principal: OperatorDep, db: DbDep) -> RoutingOverviewOut:
    """Your routes and the deployment routes. The 'default' purpose is the
    default provider/account/model; later entries are its fallbacks."""
    actor = _actor(principal)
    personal = await routing.policies_for(db, actor.user_id) if actor.user_id else []
    return RoutingOverviewOut(
        purposes=list(routing.PURPOSES),
        personal=[_policy_out(p) for p in personal],
        deployment=[_policy_out(p) for p in await routing.policies_for(db, None)],
    )


@router.put("/routing/{purpose}", response_model=RoutingPolicyOut)
async def put_routing(
    purpose: str, body: RoutingPolicyIn, principal: OperatorDep, db: DbDep
) -> RoutingPolicyOut:
    try:
        policy = await routing.set_policy(
            db,
            _actor(principal),
            scope=body.scope,
            purpose=purpose,
            chain=[(entry.account_id, entry.model) for entry in body.chain],
        )
    except AccountError as exc:
        raise _http(exc) from None
    await db.commit()
    await db.refresh(policy)
    return _policy_out(policy)


@router.delete("/routing/{purpose}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routing(
    purpose: str,
    principal: OperatorDep,
    db: DbDep,
    scope: Literal["personal", "deployment"] = "personal",
) -> None:
    try:
        found = await routing.delete_policy(db, _actor(principal), scope=scope, purpose=purpose)
    except AccountError as exc:
        raise _http(exc) from None
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such route")
    await db.commit()


# --- calling models ------------------------------------------------------------------


async def _resolve_targets(
    db: AsyncSession, actor: Actor, body: LLMTarget
) -> tuple[list[routing.Target], str | None]:
    if body.account_id is not None and body.model is not None:
        try:
            account = await require_account(db, actor, body.account_id, "use")
        except AccountError as exc:
            raise _http(exc) from None
        return [routing.Target(account, body.model)], None
    purpose = body.purpose or "default"
    return await routing.resolve(db, actor, purpose), purpose  # NoRouteError -> 409


def _chat_response(result: gateway.GatewayResult) -> ChatResponse:
    response = result.response
    return ChatResponse(
        content=response.content,
        reasoning=response.reasoning,
        tool_calls=response.tool_calls,
        finish_reason=response.finish_reason,
        provider=result.target.account.provider,
        model=result.target.model,
        account_id=result.target.account.id,
        usage=response.usage,
        latency_ms=result.latency_ms,
        attempts=result.attempts,
        fallback_index=result.fallback_index,
        estimated_cost=result.estimated_cost,
        cost_source=result.cost_source,
    )


_CHAT_FIELDS = {
    "messages",
    "system",
    "temperature",
    "max_tokens",
    "stop",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "response_format",
    "reasoning_effort",
}


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, principal: OperatorDep, db: DbDep):
    """Provider-independent chat. Target an account+model or a routing
    purpose; set `stream` for server-sent events of normalized chunks.
    Tool calls are returned to the caller; Lycosa does not execute tools."""
    actor = _actor(principal)
    targets, purpose = await _resolve_targets(db, actor, body)
    try:
        request = LLMRequest(
            model=targets[0].model,
            **{name: getattr(body, name) for name in _CHAT_FIELDS},
        )
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()[0]["msg"]) from None
    ctx = gateway.CallContext(actor=actor, purpose=purpose)
    if not body.stream:
        try:
            result = await gateway.generate(db, ctx, request, targets)
        finally:
            await db.commit()  # usage rows, success or failure
        return _chat_response(result)
    return StreamingResponse(
        _sse(ctx, request, targets),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _sse_frame(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


async def _sse(
    ctx: gateway.CallContext, request: LLMRequest, targets: list[routing.Target]
) -> AsyncIterator[str]:
    # own session: the request-scoped one may close before the body is sent
    async with get_runtime_sessionmaker()() as session:
        try:
            async for event in gateway.stream(session, ctx, request, targets):
                yield _sse_frame(event.type.value, event.model_dump_json(exclude_none=True))
            yield _sse_frame("done", "{}")
        except LLMError as exc:
            yield _sse_frame("error", json.dumps({"code": exc.code, "message": exc.public_message}))
        finally:
            try:
                await session.commit()
            except Exception:  # noqa: BLE001 — never mask the stream outcome
                logger.exception("could not persist LLM stream usage")


@router.post("/test", response_model=ChatResponse)
async def test_prompt(body: PromptCheckRequest, principal: OperatorDep, db: DbDep) -> ChatResponse:
    """Send one prompt through an account or route and return the normalized
    answer, with the provider, fallback position, usage and cost."""
    actor = _actor(principal)
    targets, purpose = await _resolve_targets(db, actor, body)
    request = LLMRequest(
        model=targets[0].model,
        messages=[Message(role="user", content=body.prompt)],
        max_tokens=body.max_tokens,
    )
    try:
        result = await gateway.generate(
            db, gateway.CallContext(actor=actor, purpose=purpose), request, targets
        )
    finally:
        await db.commit()
    return _chat_response(result)


@router.get("/usage", response_model=list[UsageOut])
async def list_usage(
    principal: OperatorDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    all_users: bool = False,
) -> list[LLMUsage]:
    """Your recent LLM usage (administrators: everyone's, with all_users)."""
    actor = _actor(principal)
    query = select(LLMUsage).order_by(LLMUsage.created_at.desc()).limit(limit)
    if not (all_users and actor.is_admin):
        query = query.where(
            LLMUsage.user_id == actor.user_id
            if actor.user_id
            else LLMUsage.api_key_id == actor.api_key_id
        )
    return list((await db.execute(query)).scalars())
