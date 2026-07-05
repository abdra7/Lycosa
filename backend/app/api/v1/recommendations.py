from fastapi import APIRouter

from app.api.deps import PrincipalDep
from app.schemas.node import HardwareProfile
from app.schemas.recommendation import RecommendationOut
from app.services.recommendation import get_recommender

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post("/node-role", response_model=RecommendationOut)
async def recommend_node_role(
    profile: HardwareProfile, _principal: PrincipalDep
) -> RecommendationOut:
    """Recommend a node role for a hardware profile.

    Transparent rule-based scoring: returns the winning role with confidence,
    a human-readable rationale, and every role's score.
    """
    recommendation = get_recommender().recommend(profile)
    return RecommendationOut(**recommendation.model_dump())
