"""Node registry service: registration (create or re-register), inventory, updates."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, Node
from app.models.node import NodeStatus
from app.schemas.node import HardwareProfile, NodePatch, NodeRegisterRequest
from app.services.audit import audit


def _apply_profile(node: Node, profile: HardwareProfile) -> None:
    node.hardware_profile = profile.model_dump()
    node.cpu_cores = profile.cpu_cores
    node.ram_gb = profile.ram_gb
    node.gpu_count = sum(gpu.count for gpu in profile.gpus)
    node.gpu_vram_gb = max((gpu.vram_gb for gpu in profile.gpus), default=None)
    node.storage_gb = profile.storage_gb
    node.os_name = profile.os.name


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
