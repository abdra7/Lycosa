from fastapi import FastAPI

from app.api.v1.router import api_v1_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Lycosa Control Plane",
    description="Distributed multi-agent AI orchestration platform — control plane API.",
    version="0.1.0",
)

app.include_router(api_v1_router)


@app.get("/healthz", tags=["health"])
async def healthz() -> dict[str, str]:
    """Liveness probe. Returns 200 when the API process is up."""
    return {"status": "ok"}
