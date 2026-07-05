import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import DbDep, Principal, require_roles
from app.core.security import generate_api_key
from app.models import ApiKey, AuditLog, Role
from app.models.user import ROLE_ADMIN
from app.schemas.apikey import ApiKeyCreate, ApiKeyCreatedOut, ApiKeyOut
from app.schemas.auth import AuditLogOut
from app.services.audit import audit

router = APIRouter(prefix="/admin", tags=["admin"])

AdminDep = Annotated[Principal, Depends(require_roles(ROLE_ADMIN))]


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
