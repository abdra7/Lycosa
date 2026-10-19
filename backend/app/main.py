import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api.v1 import events
from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.leader import get_leader_gate
from app.core.logging import request_id_var, setup_logging
from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS, metrics_app
from app.core.phantom_privacy import PhantomPrivacyMiddleware, is_phantom
from app.core.ratelimit import RateLimitMiddleware
from app.db.session import get_runtime_sessionmaker, get_sessionmaker
from app.llm.vault import get_cipher
from app.services.knowledge.ingestion import heartbeat_live_ingestions, reap_stale_ingestions
from app.services.node import sweep_offline_nodes
from app.services.orchestrator import heartbeat_live_tasks, reap_stale_tasks
from app.services.provider_registry import registry
from app.services.workflow import heartbeat_live_runs, reap_stale_runs
from app.version import APP_VERSION

logger = logging.getLogger("lycosa.lifespan")

settings = get_settings()
registry()  # fail startup on invalid deployment-owned provider aliases
get_cipher()  # load (or create, once, across workers) the LLM credential key
setup_logging(settings.log_level)


async def _offline_sweeper() -> None:
    """Background loop: mark nodes offline when heartbeats stop (ADR-011).
    Under multiple workers only the leader sweeps (ADR-028); the others keep
    bidding each interval and take over if the leader's lock expires."""
    while True:
        interval = get_settings().offline_sweep_interval_seconds
        await asyncio.sleep(interval)
        try:
            if not await get_leader_gate().try_lead(
                "offline-sweeper", ttl_seconds=max(15, interval * 3)
            ):
                continue
            async with get_sessionmaker()() as db:
                flipped = await sweep_offline_nodes(db)
                if flipped:
                    logger.info("marked %d node(s) offline", flipped)
        except Exception:
            logger.exception("offline sweep failed; will retry next interval")


async def _lease_tick() -> None:
    """Every worker renews the lease on the workflow runs and tasks it is
    executing; only the leader reaps the ones whose lease lapsed (ADR-034,
    ADR-035)."""
    interval = get_settings().workflow_heartbeat_interval_seconds
    async with get_runtime_sessionmaker()() as db:
        await heartbeat_live_runs(db)
        await heartbeat_live_tasks(db)
    if not await get_leader_gate().try_lead("lease-reaper", ttl_seconds=max(15, interval * 3)):
        return
    async with get_runtime_sessionmaker()() as db:
        reaped = await reap_stale_runs(db)
        if reaped:
            logger.warning("marked %d orphaned workflow run(s) failed", reaped)
    async with get_runtime_sessionmaker()() as db:
        reaped = await reap_stale_tasks(db)
        if reaped:
            logger.warning("marked %d orphaned task(s) failed", reaped)


async def _lease_loop() -> None:
    while True:
        await asyncio.sleep(get_settings().workflow_heartbeat_interval_seconds)
        try:
            await _lease_tick()
        except Exception:
            logger.exception("lease tick failed; will retry next interval")


async def _ingest_lease_tick() -> None:
    """Every worker renews the lease on the ingestions it is running; only the
    leader reaps `running` embedding jobs whose lease lapsed (ADR-036). This
    replaces the one-shot startup recovery (E2E-02), which a worker respawned
    after its lock expired ran against ingestions other workers still had in
    flight."""
    interval = get_settings().ingest_heartbeat_interval_seconds
    async with get_runtime_sessionmaker()() as db:
        await heartbeat_live_ingestions(db)
    if not await get_leader_gate().try_lead("ingest-reaper", ttl_seconds=max(15, interval * 3)):
        return
    async with get_runtime_sessionmaker()() as db:
        reaped = await reap_stale_ingestions(db)
        if reaped:
            logger.warning("marked %d orphaned document ingestion(s) failed", reaped)


async def _ingest_lease_loop() -> None:
    while True:
        await asyncio.sleep(get_settings().ingest_heartbeat_interval_seconds)
        try:
            await _ingest_lease_tick()
        except Exception:
            logger.exception("ingest lease tick failed; will retry next interval")


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(_offline_sweeper()),
        asyncio.create_task(_lease_loop()),
        asyncio.create_task(_ingest_lease_loop()),
    ]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(
    title="Lycosa Control Plane",
    description="Distributed multi-agent AI orchestration platform — control plane API.",
    version=APP_VERSION,
    lifespan=lifespan,
)

register_error_handlers(app)
app.add_middleware(RateLimitMiddleware)


@app.middleware("http")
async def observability_middleware(request: Request, call_next) -> Response:
    """Request id for log correlation + HTTP metrics per route template."""
    if is_phantom(request.url.path):
        return await call_next(request)
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


app.add_middleware(PhantomPrivacyMiddleware)


app.include_router(api_v1_router)
app.include_router(events.router, prefix="/api/v1")
app.mount("/metrics", metrics_app())


@app.get("/healthz", tags=["health"])
async def healthz() -> dict[str, str]:
    """Liveness probe. Returns 200 when the API process is up."""
    return {"status": "ok", "version": APP_VERSION}
