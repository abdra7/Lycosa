import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.ratelimit import RateLimitMiddleware
from app.db.session import get_sessionmaker
from app.services.node import sweep_offline_nodes

logger = logging.getLogger("lycosa.lifespan")

settings = get_settings()


async def _offline_sweeper() -> None:
    """Background loop: mark nodes offline when heartbeats stop (ADR-011)."""
    while True:
        await asyncio.sleep(get_settings().offline_sweep_interval_seconds)
        try:
            async with get_sessionmaker()() as db:
                flipped = await sweep_offline_nodes(db)
                if flipped:
                    logger.info("marked %d node(s) offline", flipped)
        except Exception:
            logger.exception("offline sweep failed; will retry next interval")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_offline_sweeper())
    yield
    task.cancel()


app = FastAPI(
    title="Lycosa Control Plane",
    description="Distributed multi-agent AI orchestration platform — control plane API.",
    version="0.1.0",
    lifespan=lifespan,
)

register_error_handlers(app)
app.add_middleware(RateLimitMiddleware)

app.include_router(api_v1_router)


@app.get("/healthz", tags=["health"])
async def healthz() -> dict[str, str]:
    """Liveness probe. Returns 200 when the API process is up."""
    return {"status": "ok"}
