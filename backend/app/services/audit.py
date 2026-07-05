"""Audit trail writer. Call inside the same transaction as the action being audited."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def audit(
    db: AsyncSession,
    *,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    actor_api_key_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        actor_user_id=actor_user_id,
        actor_api_key_id=actor_api_key_id,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
        ip_address=ip_address,
    )
    db.add(entry)
    await db.flush()
    return entry
