from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskType
from app.services.scheduler import rank_candidates
from tests.conftest import make_node


async def test_role_match_beats_hybrid(db_session: AsyncSession) -> None:
    hybrid = await make_node(db_session, "hybrid-box", role="hybrid", gpu_vram_gb=24)
    compute = await make_node(db_session, "compute-box", role="ai_compute", gpu_vram_gb=24)

    candidates = await rank_candidates(db_session, TaskType.CODING)
    assert [n.id for n in candidates] == [compute.id, hybrid.id]


async def test_recommended_role_used_when_unassigned(db_session: AsyncSession) -> None:
    node = await make_node(db_session, "fresh-box", role=None, recommended_role="knowledge")
    candidates = await rank_candidates(db_session, TaskType.RETRIEVAL)
    assert [n.id for n in candidates] == [node.id]


async def test_offline_and_agentless_nodes_excluded(db_session: AsyncSession) -> None:
    await make_node(db_session, "offline-box", role="ai_compute", status="offline")
    await make_node(db_session, "no-agent-box", role="ai_compute", agent_url=None)
    await make_node(db_session, "no-token-box", role="ai_compute", agent_token=None)

    assert await rank_candidates(db_session, TaskType.CODING) == []


async def test_lower_load_wins_within_same_role(db_session: AsyncSession) -> None:
    busy = await make_node(
        db_session, "busy", role="hybrid", metrics={"cpu_percent": 95, "running_tasks": 3}
    )
    idle = await make_node(
        db_session, "idle", role="hybrid", metrics={"cpu_percent": 5, "running_tasks": 0}
    )

    candidates = await rank_candidates(db_session, TaskType.GENERAL)
    assert [n.id for n in candidates] == [idle.id, busy.id]


async def test_model_availability_boosts_rank(db_session: AsyncSession) -> None:
    without = await make_node(db_session, "without-model", role="hybrid", models=["phi3:mini"])
    with_model = await make_node(db_session, "with-model", role="hybrid", models=["llama3:70b"])

    candidates = await rank_candidates(db_session, TaskType.GENERAL, model="llama3:70b")
    assert [n.id for n in candidates] == [with_model.id, without.id]


async def test_exclude_skips_already_attempted(db_session: AsyncSession) -> None:
    first = await make_node(db_session, "first", role="tool")
    second = await make_node(db_session, "second", role="tool")

    candidates = await rank_candidates(db_session, TaskType.TOOL, exclude={first.id})
    assert [n.id for n in candidates] == [second.id]


async def test_wrong_role_not_scheduled(db_session: AsyncSession) -> None:
    await make_node(db_session, "storage-box", role="storage")
    assert await rank_candidates(db_session, TaskType.CODING) == []
