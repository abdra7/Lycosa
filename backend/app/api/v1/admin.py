from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import DbDep, Principal, require_roles
from app.models import AuditLog
from app.models.user import ROLE_ADMIN
from app.schemas.auth import AuditLogOut

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/audit-logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    db: DbDep,
    _principal: Annotated[Principal, Depends(require_roles(ROLE_ADMIN))],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[AuditLog]:
    """Most recent audit entries. Admin only."""
    result = await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))
    return list(result.scalars())
