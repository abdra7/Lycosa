import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, SecretStr
from sqlalchemy import select

from app.api.deps import DbDep, Principal, require_roles
from app.core.config import get_settings
from app.core.security import generate_api_key
from app.models import ApiKey, AuditLog, Role
from app.models.user import ROLE_ADMIN
from app.schemas.apikey import ApiKeyCreate, ApiKeyCreatedOut, ApiKeyOut
from app.schemas.auth import AuditLogOut
from app.services.audit import audit
from app.services.openrouter import MODEL as OPENROUTER_MODEL
from app.services.provider_registry import registry
from app.services.provider_secrets import (
    SecretStoreUnavailable,
    manage_key,
    provider_key,
    set_session_key,
)

router = APIRouter(prefix="/admin", tags=["admin"])

AdminDep = Annotated[Principal, Depends(require_roles(ROLE_ADMIN))]


@router.get("/providers")
async def list_providers(_principal: AdminDep) -> list[dict]:
    """Safe registry metadata; configuration is not proof of provider validity."""
    settings = get_settings()
    result = []
    for name, provider in registry().items():
        configured = None
        if provider.execution_mode == "cloud":
            try:
                configured = bool(provider_key(name))
            except SecretStoreUnavailable:
                configured = False
        result.append(
            {
                **asdict(provider),
                "credential_configured": configured,
                "models": [OPENROUTER_MODEL]
                if name == "openrouter"
                else (
                    settings.provider_profiles[name].models
                    if name in settings.provider_profiles
                    else (settings.cloud_models if provider.execution_mode == "cloud" else [])
                ),
                "availability": "evaluated at controller dispatch"
                if name == "openrouter" or provider.adapter == "litellm"
                else "evaluated per node at dispatch",
            }
        )
    return result


class ProviderKeyInput(BaseModel):
    key: SecretStr
    storage: Literal["vault", "session"] = "vault"


@router.put("/providers/{provider}/credential", status_code=204)
async def save_provider_credential(
    provider: str,
    body: ProviderKeyInput,
    principal: AdminDep,
    db: DbDep,
) -> None:
    # Transport is enforced by the desktop client (HTTPS except loopback).
    # Deploy the API behind TLS for any remote admin access.
    _cloud_provider(provider)
    key = body.key.get_secret_value()
    if (
        not key
        or key != key.strip()
        or len(key) > 4096
        or any(ord(c) < 33 or ord(c) > 126 for c in key)
    ):
        raise HTTPException(422, "Invalid provider credential")
    try:
        if body.storage == "session":
            set_session_key(provider, key)
        else:
            manage_key(provider, key)
            set_session_key(provider, None) if get_settings().workers == 1 else None
    except SecretStoreUnavailable:
        raise HTTPException(
            503, "Secure storage unavailable; session storage requires one worker"
        ) from None
    finally:
        key = None
    await audit(
        db,
        action="provider.credential.set",
        actor_user_id=principal.id,
        resource_type="provider",
        resource_id=provider,
        detail={"storage": body.storage},
    )
    await db.commit()


@router.delete("/providers/{provider}/credential", status_code=204)
async def delete_provider_credential(
    provider: str,
    principal: AdminDep,
    db: DbDep,
    storage: Literal["vault", "session"] = "vault",
) -> None:
    _cloud_provider(provider)
    try:
        if storage == "session":
            set_session_key(provider, None)
        else:
            manage_key(provider, None)
    except SecretStoreUnavailable:
        raise HTTPException(503, "Credential removal failed for the selected storage") from None
    await audit(
        db,
        action="provider.credential.delete",
        actor_user_id=principal.id,
        resource_type="provider",
        resource_id=provider,
        detail={"storage": storage},
    )
    await db.commit()


def _cloud_provider(name: str) -> None:
    p = registry().get(name)
    if p is None or p.execution_mode != "cloud":
        raise HTTPException(422, "Unknown cloud provider")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/audit-logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    db: DbDep,
    _principal: AdminDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[AuditLog]:
    """Most recent audit entries. Admin only."""
    result = await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))
    return list(result.scalars())


@router.post("/api-keys", response_model=ApiKeyCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreate, principal: AdminDep, request: Request, db: DbDep
) -> ApiKeyCreatedOut:
    """Mint an API key (typically node-role, for agent installs).

    The full key appears in this response only; store it safely — the server
    keeps just a prefix and a hash.
    """
    role = (await db.execute(select(Role).where(Role.name == body.role))).scalar_one_or_none()
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role {body.role!r} is not seeded in this deployment",
        )
    full_key, prefix, key_hash = generate_api_key()
    record = ApiKey(
        key_prefix=prefix,
        key_hash=key_hash,
        name=body.name,
        role_id=role.id,
        expires_at=body.expires_at,
    )
    db.add(record)
    await db.flush()
    await audit(
        db,
        action="apikey.create",
        actor_user_id=principal.id,
        resource_type="api_key",
        resource_id=str(record.id),
        detail={"name": body.name, "role": body.role},
        ip_address=_client_ip(request),
    )
    await db.commit()
    await db.refresh(record)
    return ApiKeyCreatedOut(
        **ApiKeyOut.model_validate(record).model_dump(), api_key=full_key, role=body.role
    )


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(db: DbDep, _principal: AdminDep) -> list[ApiKey]:
    """All keys, newest first — prefixes only, never secrets."""
    result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return list(result.scalars())


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: uuid.UUID, principal: AdminDep, request: Request, db: DbDep
) -> None:
    """Revoke a key immediately. Revocation also severs a bound node's access."""
    record = (await db.execute(select(ApiKey).where(ApiKey.id == key_id))).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    if record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        await audit(
            db,
            action="apikey.revoke",
            actor_user_id=principal.id,
            resource_type="api_key",
            resource_id=str(record.id),
            detail={"name": record.name},
            ip_address=_client_ip(request),
        )
    await db.commit()
