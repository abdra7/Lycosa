"""Capability- and load-aware node selection (SDD FR-5).

Returns an ordered candidate list; the orchestrator walks it for failover.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Node, NodeStatus
from app.models.task import TaskType
from app.services.classifier import preferred_roles


def _effective_role(node: Node) -> str | None:
    """Operator-assigned role wins; unassigned nodes fall back to the
    recommendation so a fresh fabric is schedulable out of the box."""
    return node.role or node.recommended_role


def _node_models(node: Node) -> list[str]:
    profile = node.hardware_profile or {}
    return [m for r in profile.get("runtimes", []) for m in r.get("models", [])]


def _score(node: Node, role_rank: int, total_roles: int, model: str | None) -> float:
    # role preference dominates; resources break ties; load penalizes
    score = float((total_roles - role_rank) * 100)
    score += min(node.ram_gb or 0, 128) / 128 * 10
    score += min(node.gpu_vram_gb or 0, 48) / 48 * 10
    if model is not None and model in _node_models(node):
        score += 20
    metrics = node.metrics or {}
    score -= float(metrics.get("cpu_percent", 0)) / 10
    score -= float(metrics.get("running_tasks", 0)) * 5
    return score


async def rank_candidates(
    db: AsyncSession,
    task_type: TaskType,
    model: str | None = None,
    exclude: set[uuid.UUID] | None = None,
) -> list[Node]:
    """Online, dispatchable nodes whose role fits the task, best first."""
    roles = preferred_roles(task_type)
    exclude = exclude or set()

    online = (
        (
            await db.execute(
                select(Node).where(
                    Node.status == NodeStatus.ONLINE,
                    Node.agent_url.is_not(None),
                    Node.agent_token.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )

    scored: list[tuple[float, Node]] = []
    for node in online:
        if node.id in exclude:
            continue
        role = _effective_role(node)
        if role not in roles:
            continue
        scored.append((_score(node, roles.index(role), len(roles), model), node))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [node for _, node in scored]
