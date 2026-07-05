"""Node registry service: registration, inventory, updates, liveness."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.events import get_event_bus
from app.core.metrics import NODE_CPU, NODE_RAM, NODE_TASKS, NODES
from app.models import ApiKey, Node
from app.models.node import NodeStatus
from app.schemas.node import HardwareProfile, NodeMetrics, NodePatch, NodeRegisterRequest
from app.services.audit import audit
from app.services.recommendation import get_recommender


def _apply_profile(node: Node, profile: HardwareProfile) -> None:
    node.hardware_profile = profile.model_dump()
    node.cpu_cores = profile.cpu_cores
    node.ram_gb = profile.ram_gb
    node.gpu_count = sum(gpu.count for gpu in profile.gpus)
    node.gpu_vram_gb = max((gpu.vram_gb for gpu in profile.gpus), default=None)
    node.storage_gb = profile.storage_gb
    node.os_name = profile.os.name

    # recompute the recommendation on every (re-)registration; the assigned
    # `role` is operator-owned and never modified here
    recommendation = get_recommender().recommend(profile)
    node.recommended_role = recommendation.role
    node.recommendation_confidence = recommendation.confidence
    node.recommendation_rationale = recommendation.rationale


async def register_node(
    db: AsyncSession,
    body: NodeRegisterRequest,
    api_key_id: uuid.UUID,
    ip_address: str | None,
) -> tuple[Node, bool]:
    """Register the node for this API key. One key = one node identity:
    an unbound key creates and binds a node; a bound key re-registers
    (updates) its node. Returns (node, created)."""
    api_key = (await db.execute(select(ApiKey).where(ApiKey.id == api_key_id))).scalar_one()

    if api_key.node_id is not None:
        node = (await db.execute(select(Node).where(Node.id == api_key.node_id))).scalar_one()
        node.name = body.name
        _apply_profile(node, body.hardware_profile)
        created = False
    else:
        node = Node(name=body.name, status=NodeStatus.REGISTERED)
        _apply_profile(node, body.hardware_profile)
        db.add(node)
        await db.flush()
        api_key.node_id = node.id
        created = True

    if body.agent_url is not None:
        node.agent_url = body.agent_url
    if body.agent_token is not None:
        node.agent_token = body.agent_token

    await audit(
        db,
        action="node.register" if created else "node.reregister",
        actor_api_key_id=api_key_id,
        resource_type="node",
        resource_id=str(node.id),
        detail={"name": node.name},
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(node)
    return node, created


async def list_nodes(db: AsyncSession, status: NodeStatus | None = None) -> list[Node]:
    query = select(Node).order_by(Node.created_at)
    if status is not None:
        query = query.where(Node.status == status)
    return list((await db.execute(query)).scalars())


async def get_node(db: AsyncSession, node_id: uuid.UUID) -> Node | None:
    return (await db.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()


async def patch_node(
    db: AsyncSession,
    node: Node,
    patch: NodePatch,
    actor_user_id: uuid.UUID | None,
    ip_address: str | None,
) -> Node:
    changes: dict[str, str] = {}
    if patch.name is not None and patch.name != node.name:
        changes["name"] = patch.name
        node.name = patch.name
    if patch.role is not None and patch.role.value != node.role:
        changes["role"] = patch.role.value
        node.role = patch.role.value

    if changes:
        await audit(
            db,
            action="node.update",
            actor_user_id=actor_user_id,
            resource_type="node",
            resource_id=str(node.id),
            detail=changes,
            ip_address=ip_address,
        )
    await db.commit()
    await db.refresh(node)
    return node


async def _update_node_gauges(db: AsyncSession) -> None:
    rows = (
        await db.execute(select(Node.status, sa_func.count(Node.id)).group_by(Node.status))
    ).all()
    counts = {status.value: count for status, count in rows}
    for status in NodeStatus:
        NODES.labels(status.value).set(counts.get(status.value, 0))


async def record_heartbeat(
    db: AsyncSession, node: Node, metrics: NodeMetrics, api_key_id: uuid.UUID
) -> None:
    """Store latest metrics and mark the node online. Only the offline→online
    transition is audited; individual heartbeats would be noise."""
    came_online = node.status != NodeStatus.ONLINE
    node.last_heartbeat_at = datetime.now(UTC)
    node.metrics = metrics.model_dump()
    node.status = NodeStatus.ONLINE
    if came_online:
        await audit(
            db,
            action="node.online",
            actor_api_key_id=api_key_id,
            resource_type="node",
            resource_id=str(node.id),
        )
    await db.commit()

    bus = get_event_bus()
    if came_online:
        bus.publish("node.connected", {"node_id": str(node.id), "name": node.name})
    bus.publish(
        "node.metrics.updated",
        {"node_id": str(node.id), "name": node.name, "metrics": node.metrics},
    )
    NODE_CPU.labels(node.name).set(metrics.cpu_percent)
    NODE_RAM.labels(node.name).set(metrics.ram_percent)
    NODE_TASKS.labels(node.name).set(metrics.running_tasks)
    await _update_node_gauges(db)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def sweep_offline_nodes(db: AsyncSession) -> int:
    """Flip online nodes to offline when their last heartbeat is older than
    HEARTBEAT_TIMEOUT_SECONDS. Returns how many were flipped. Runs from the
    lifespan background task (and directly from tests)."""
    timeout = timedelta(seconds=get_settings().heartbeat_timeout_seconds)
    cutoff = datetime.now(UTC) - timeout

    online = list(
        (await db.execute(select(Node).where(Node.status == NodeStatus.ONLINE))).scalars()
    )
    flipped = 0
    bus = get_event_bus()
    for node in online:
        if node.last_heartbeat_at is None or _as_utc(node.last_heartbeat_at) < cutoff:
            node.status = NodeStatus.OFFLINE
            await audit(
                db,
                action="node.offline",
                resource_type="node",
                resource_id=str(node.id),
                detail={"reason": "heartbeat timeout"},
            )
            flipped += 1
            bus.publish("node.disconnected", {"node_id": str(node.id), "name": node.name})
            bus.publish(
                "alert.created",
                {
                    "severity": "warning",
                    "message": f"Node {node.name!r} went offline (heartbeat timeout)",
                    "node_id": str(node.id),
                },
            )
    if flipped:
        await db.commit()
        await _update_node_gauges(db)
    return flipped
