from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Principal, require_roles
from app.core.config import get_settings
from app.models.user import ROLE_ADMIN, ROLE_OPERATOR
from app.services.provider_registry import registry

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("")
async def catalog(
    _principal: Annotated[Principal, Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR))],
) -> list[dict]:
    """Operator-safe policy catalogue: no secrets, endpoints or key status."""
    profiles = get_settings().provider_profiles
    return [
        {
            **asdict(p),
            "models": profiles[name].models if name in profiles else [],
            "policy_configured": name in profiles or p.adapter == "native",
        }
        for name, p in registry().items()
    ]
