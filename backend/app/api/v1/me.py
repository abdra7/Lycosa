from fastapi import APIRouter

from app.api.deps import PrincipalDep
from app.schemas.auth import PrincipalOut

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=PrincipalOut)
async def me(principal: PrincipalDep) -> PrincipalOut:
    """Who am I? Works for both bearer-token users and API-key callers."""
    return PrincipalOut(
        type=principal.type,  # type: ignore[arg-type]
        id=principal.id,
        role=principal.role,
        email=principal.email,
        name=principal.name,
    )
