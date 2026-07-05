import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import DbDep, Principal, PrincipalDep, require_roles
from app.core.config import get_settings
from app.models import ApiKey
from app.models.node import NodeStatus
from app.models.user import ROLE_ADMIN, ROLE_NODE, ROLE_OPERATOR
from app.schemas.node import (
    HeartbeatRequest,
    HeartbeatResponse,
    NodeOut,
    NodePatch,
    NodeRegisterRequest,
)
from app.services import node as node_service

router = APIRouter(prefix="/nodes", tags=["nodes"])

OperatorDep = Annotated[Principal, Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR))]


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/register", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: NodeRegisterRequest,
    principal: PrincipalDep,
    request: Request,
    response: Response,
    db: DbDep,
) -> NodeOut:
    """Register this node with its hardware profile.

    Requires a node-role API key. A key already bound to a node re-registers
    (updates) that node and returns 200; an unbound key creates one (201).
    """
    if principal.type != "api_key" or principal.role != ROLE_NODE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Node registration requires a node-role API key",
        )
    node, created = await node_service.register_node(
        db, body, api_key_id=principal.id, ip_address=_client_ip(request)
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return NodeOut.model_validate(node)


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    body: HeartbeatRequest, principal: PrincipalDep, db: DbDep
) -> HeartbeatResponse:
    """Agent liveness ping with current metrics. Requires a node API key
    that has already registered (is bound to a node)."""
    if principal.type != "api_key" or principal.role != ROLE_NODE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Heartbeat requires a node-role API key",
        )
    api_key = (await db.execute(select(ApiKey).where(ApiKey.id == principal.id))).scalar_one()
    if api_key.node_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="API key is not bound to a node; register first",
        )
    node = await node_service.get_node(db, api_key.node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    await node_service.record_heartbeat(db, node, body.metrics, api_key_id=principal.id)
    return HeartbeatResponse(
        heartbeat_interval_seconds=get_settings().agent_heartbeat_interval_seconds
    )


@router.get("", response_model=list[NodeOut])
async def list_nodes(
    db: DbDep,
    _principal: OperatorDep,
    status_filter: Annotated[NodeStatus | None, Query(alias="status")] = None,
) -> list[NodeOut]:
    """Node inventory, optionally filtered by status."""
    nodes = await node_service.list_nodes(db, status=status_filter)
    return [NodeOut.model_validate(n) for n in nodes]


@router.get("/{node_id}", response_model=NodeOut)
async def get_node(node_id: uuid.UUID, db: DbDep, _principal: OperatorDep) -> NodeOut:
    node = await node_service.get_node(db, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return NodeOut.model_validate(node)


@router.patch("/{node_id}", response_model=NodeOut)
async def patch_node(
    node_id: uuid.UUID,
    patch: NodePatch,
    principal: OperatorDep,
    request: Request,
    db: DbDep,
) -> NodeOut:
    """Update a node's assigned role or name (operator/admin). Audited."""
    node = await node_service.get_node(db, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    actor_user_id = principal.id if principal.type == "user" else None
    node = await node_service.patch_node(
        db, node, patch, actor_user_id=actor_user_id, ip_address=_client_ip(request)
    )
    return NodeOut.model_validate(node)
