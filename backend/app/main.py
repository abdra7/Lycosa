import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from prometheus_client import make_asgi_app

from app.api.v1 import events
from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import request_id_var, setup_logging
from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS
from app.core.ratelimit import RateLimitMiddleware
from app.db.session import get_sessionmaker
from app.services.node import sweep_offline_nodes

logger = logging.getLogger("lycosa.lifespan")

settings = get_settings()
setup_logging(settings.log_level)


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


@app.middleware("http")
async def observability_middleware(request: Request, call_next) -> Response:
    """Request id for log correlation + HTTP metrics per route template."""
    request_id = uuid.uuid4().hex[:16]
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    route = request.scope.get("route")
    path = getattr(route, "path", "unmatched")
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    HTTP_DURATION.labels(request.method, path).observe(time.perf_counter() - started)
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(api_v1_router)
app.include_router(events.router, prefix="/api/v1")
app.mount("/metrics", make_asgi_app())


@app.get("/healthz", tags=["health"])
async def healthz() -> dict[str, str]:
    """Liveness probe. Returns 200 when the API process is up."""
    return {"status": "ok"}
