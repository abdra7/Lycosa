from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.models.task import TaskType
from app.schemas.task import TaskCreate
from app.services.scheduler import route_candidates
from tests.conftest import make_node


async def test_gpu_load_outweighs_cpu(db_session):
    for name, cpu, util, used in [("busy", 15, 96, 11000), ("free", 35, 30, 5000)]:
        await make_node(
            db_session,
            name,
            role="hybrid",
            metrics={
                "cpu_percent": cpu,
                "ram_percent": 40,
                "gpus": [
                    {
                        "index": 0,
                        "memory_total_mb": 12000,
                        "memory_used_mb": used,
                        "utilization_percent": util,
                    }
                ],
            },
        )
    results = await route_candidates(db_session, TaskType.GENERAL, TaskCreate(prompt="hello"))
    assert results[0].node.name == "free"
    assert results[0].explain()["execution_mode"] == "local"


async def test_privacy_excludes_cloud(db_session):
    assert (
        await route_candidates(
            db_session,
            TaskType.GENERAL,
            TaskCreate(prompt="private", provider="anthropic", requires_privacy=True),
            credentials_available=True,
        )
        == []
    )


async def test_known_insufficient_vram_and_multi_gpu_not_summed(db_session):
    node = await make_node(
        db_session,
        "two-cards",
        role="hybrid",
        metrics={
            "gpus": [
                {"index": i, "memory_total_mb": 8000, "memory_used_mb": 4000} for i in range(2)
            ]
        },
    )
    node.last_heartbeat_at = datetime.now(UTC)
    await db_session.commit()
    assert (
        await route_candidates(
            db_session, TaskType.GENERAL, TaskCreate(prompt="hello", required_vram_mb=6000)
        )
        == []
    )


async def test_missing_model_and_stale_heartbeat_excluded(db_session):
    node = await make_node(db_session, "stale", role="hybrid")
    node.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    await db_session.commit()
    assert await route_candidates(db_session, TaskType.GENERAL, TaskCreate(prompt="hi")) == []
    node.last_heartbeat_at = datetime.now(UTC)
    await db_session.commit()
    assert (
        await route_candidates(
            db_session, TaskType.GENERAL, TaskCreate(prompt="hi", model="missing")
        )
        == []
    )


async def test_cloud_requires_pinned_origin_and_approval(db_session, monkeypatch):
    node = await make_node(db_session, "cloud", role="hybrid", agent_url="https://cloud:8010")
    node.last_heartbeat_at = datetime.now(UTC)
    node.hardware_profile = {"extra": {"cloud_execution_capability": True}}
    await db_session.commit()
    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_models", ["model"])
    monkeypatch.setattr(settings, "cloud_allowed_node_ids", [str(node.id)])
    monkeypatch.setattr(settings, "cloud_node_origins", {str(node.id): node.agent_url})
    body = TaskCreate(prompt="hi", provider="anthropic", model="model")
    result = await route_candidates(db_session, TaskType.GENERAL, body, credentials_available=True)
    assert len(result) == 1
    node.agent_url = "https://unapproved:8010"
    await db_session.commit()
    assert (
        await route_candidates(db_session, TaskType.GENERAL, body, credentials_available=True) == []
    )


async def test_explicit_cpu_fallback_requires_ram(db_session):
    node = await make_node(
        db_session,
        "cpu",
        role="hybrid",
        metrics={"ram_available_mb": 12000, "gpus": [], "runtime_health": {"ollama": True}},
    )
    node.last_heartbeat_at = datetime.now(UTC)
    await db_session.commit()
    body = TaskCreate(
        prompt="hi", required_vram_mb=10000, required_ram_mb=10000, allow_cpu_fallback=True
    )
    candidates = await route_candidates(db_session, TaskType.GENERAL, body)
    assert len(candidates) == 1 and candidates[0].cpu_only
    body.required_ram_mb = 15000
    assert await route_candidates(db_session, TaskType.GENERAL, body) == []


async def test_unhealthy_runtime_excluded(db_session):
    await make_node(
        db_session, "dead-runtime", role="hybrid", metrics={"runtime_health": {"ollama": False}}
    )
    assert await route_candidates(db_session, TaskType.GENERAL, TaskCreate(prompt="hi")) == []
