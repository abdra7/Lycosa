from pydantic import BaseModel

from app.models.node import NodeRole


class RecommendationOut(BaseModel):
    role: NodeRole
    confidence: float
    rationale: list[str]
    scores: dict[str, float]
