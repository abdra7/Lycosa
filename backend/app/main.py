from fastapi import FastAPI

from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.ratelimit import RateLimitMiddleware

settings = get_settings()

app = FastAPI(
    title="Lycosa Control Plane",
    description="Distributed multi-agent AI orchestration platform — control plane API.",
    version="0.1.0",
)

register_error_handlers(app)
app.add_middleware(RateLimitMiddleware)

app.include_router(api_v1_router)


@app.get("/healthz", tags=["health"])
async def healthz() -> dict[str, str]:
    """Liveness probe. Returns 200 when the API process is up."""
    return {"status": "ok"}
