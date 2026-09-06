"""Capability- and load-aware node selection (SDD FR-5).

Returns an ordered candidate list; the orchestrator walks it for failover.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Node, NodeStatus
from app.models.task import TaskType
from app.schemas.task import TaskCreate
from app.services.classifier import preferred_roles
from app.services.llm_recommendation import _load_catalog
from app.services.provider_registry import PROVIDERS


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
    score -= float(metrics.get("ram_percent", 0)) / 10
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


@dataclass
class RoutingDecision:
    node: Node
    model: str
    provider: str
    score: float
    reasons: list[str]
    gpu_index: int | None = None
    cpu_only: bool = False

    def explain(self) -> dict:
        return {
            "selected_node": str(self.node.id),
            "model": self.model,
            "provider": self.provider,
            "execution_mode": PROVIDERS[self.provider].execution_mode,
            "score": self.score,
            "reasons": self.reasons,
            "gpu_index": self.gpu_index,
            "cpu_only": self.cpu_only,
        }


async def route_candidates(
    db: AsyncSession, task_type: TaskType, body: TaskCreate, *, credentials_available: bool = False
) -> list[RoutingDecision]:
    settings = get_settings()
    descriptor = PROVIDERS[body.provider]
    cloud = descriptor.execution_mode == "cloud"
    if cloud and (
        body.requires_privacy
        or not credentials_available
        or body.model not in settings.cloud_models
    ):
        return []
    nodes = await rank_candidates(db, task_type, model=body.model)
    decisions = []
    for node in nodes:
        metrics = node.metrics or {}
        profile = node.hardware_profile or {}
        if node.last_heartbeat_at:
            last = (
                node.last_heartbeat_at.replace(tzinfo=UTC)
                if (node.last_heartbeat_at.tzinfo is None)
                else node.last_heartbeat_at
            )
            if (datetime.now(UTC) - last).total_seconds() > settings.heartbeat_timeout_seconds:
                continue
        elif body.required_vram_mb is not None or cloud:
            continue
        models = _node_models(node)
        model = body.model or next(iter(models), None)
        if not model:
            continue
        reasons = ["online dispatchable node", "CPU, RAM and active task load evaluated"]
        roles = preferred_roles(task_type)
        score = (len(roles) - roles.index(_effective_role(node))) * 100
        score -= float(metrics.get("cpu_percent", 0)) * 0.1
        reasons.append(f"compatible node role: {_effective_role(node)}")
        score -= float(metrics.get("ram_percent", 0)) * 0.2
        score -= float(metrics.get("running_tasks", 0)) * 10
        selected_gpu = None
        cpu_only = False
        if cloud:
            if (
                str(node.id) not in settings.cloud_allowed_node_ids
                or not node.agent_url.startswith("https://")
                or settings.cloud_node_origins.get(str(node.id)) != node.agent_url
                or not profile.get("extra", {}).get("cloud_execution_capability")
            ):
                continue
            reasons += [
                "cloud provider enabled with controller credential",
                "explicitly approved cloud node with HTTPS transport",
            ]
        else:
            if metrics.get("runtime_health", {}).get("ollama") is False:
                continue
            if model not in models:
                continue
            reasons.append("model installed on node")
            if body.requires_privacy:
                reasons.append("privacy policy requires local execution")
            available_ram = metrics.get("ram_available_mb")
            entry = next((item for item in _load_catalog() if item["name"] == model), {})
            required_vram = body.required_vram_mb
            if required_vram is None and metrics.get("gpus") and entry:
                required_vram = int(entry["min_vram_gb"] * 1024)
                reasons.append("VRAM estimate from configured quantized model catalog")
            if body.required_ram_mb is not None and (
                available_ram is None or available_ram < body.required_ram_mb
            ):
                continue
            candidates = []
            for gpu in metrics.get("gpus", []):
                total, used = gpu.get("memory_total_mb"), gpu.get("memory_used_mb")
                if not gpu.get("available", True) or total is None or used is None:
                    continue
                free = max(0, total - used)
                if required_vram is not None and free < (
                    required_vram + settings.routing_vram_safety_margin_mb
                ):
                    continue
                util = gpu.get("utilization_percent", gpu.get("util_percent"))
                gpu_score = free / total * 60 - (util if util is not None else 100) * 0.4
                candidates.append((gpu_score, gpu.get("index"), free))
            if required_vram is not None and len(candidates) != len(metrics.get("gpus", [])):
                # Ollama currently chooses placement itself. Do not claim a safe
                # assignment based on a different, idle GPU it might not use.
                candidates = []
            if candidates:
                choose = min if required_vram is not None else max
                gpu_score, selected_gpu, free = choose(candidates, key=lambda item: item[0])
                score += gpu_score
                reasons.append(f"GPU {selected_gpu} has {free:.0f} MiB free VRAM")
            elif required_vram is not None:
                required_ram = body.required_ram_mb or (
                    int(entry["min_ram_gb"] * 1024) if entry else None
                )
                if (
                    not body.allow_cpu_fallback
                    or required_ram is None
                    or available_ram is None
                    or available_ram < required_ram
                ):
                    continue
                cpu_only = True
                reasons.append("explicit CPU fallback with sufficient available RAM")
            else:
                reasons.append("GPU measurements unavailable or CPU fallback allowed")
        decisions.append(
            RoutingDecision(node, model, body.provider, score, reasons, selected_gpu, cpu_only)
        )
    return sorted(decisions, key=lambda decision: decision.score, reverse=True)
