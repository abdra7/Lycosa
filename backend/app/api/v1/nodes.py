import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import DbDep, Principal, PrincipalDep, require_roles
from app.core.config import get_settings
from app.models import ApiKey
from app.models.node import NodeStatus
from app.models.user import ROLE_ADMIN, ROLE_NODE, ROLE_OPERATOR
from app.schemas.node import (
    HeartbeatRequest,
    HeartbeatResponse,
    ModelInstallRequest,
    ModelInstallResponse,
    NodeOut,
    NodePatch,
    NodeRegisterRequest,
)
from app.services import node as node_service
from app.services.audit import audit
from app.services.llm_recommendation import ModelRecommendation, recommend_models

AGENT_TOKEN_HEADER = "X-Agent-Token"
# Ollama pulls download multi-GB weights; give them room before giving up.
MODEL_PULL_TIMEOUT_SECONDS = 900.0

router = APIRouter(prefix="/nodes", tags=["nodes"])

OperatorDep = Annotated[Principal, Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR))]
AdminDep = Annotated[Principal, Depends(require_roles(ROLE_ADMIN))]


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


def _installed_models(node) -> set[str]:
    profile = node.hardware_profile or {}
    return {model for runtime in profile.get("runtimes", []) for model in runtime.get("models", [])}


@router.get("/{node_id}/llm-recommendations", response_model=list[ModelRecommendation])
async def llm_recommendations(
    node_id: uuid.UUID, db: DbDep, principal: PrincipalDep
) -> list[ModelRecommendation]:
    """Which local LLMs this node's hardware can run, ranked: best runnable
    pick per use case first, with a human-readable reason for every entry.

    Operators/admins may inspect any node; a node-role key may read only its
    own node's recommendations (the agent's zero-config model setup)."""
    if principal.role == ROLE_NODE:
        api_key = (await db.execute(select(ApiKey).where(ApiKey.id == principal.id))).scalar_one()
        if api_key.node_id != node_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A node key may only read its own node's recommendations",
            )
    elif principal.role not in (ROLE_ADMIN, ROLE_OPERATOR):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    node = await node_service.get_node(db, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return recommend_models(
        ram_gb=node.ram_gb,
        gpu_vram_gb=node.gpu_vram_gb,
        installed_models=_installed_models(node),
    )


@router.post("/{node_id}/models", response_model=ModelInstallResponse)
async def install_model(
    node_id: uuid.UUID,
    body: ModelInstallRequest,
    principal: OperatorDep,
    request: Request,
    db: DbDep,
) -> ModelInstallResponse:
    """Configure the node's agent with a model: the agent pulls it via its
    runtime (Ollama), and the node's installed-model inventory is refreshed.
    Audited."""
    node = await node_service.get_node(db, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    if not node.agent_url or not node.agent_token:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Node has no agent exec API registered — run lycosa-agent on it first",
        )
    if node.status != NodeStatus.ONLINE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Node is {node.status.value}; the agent must be online to install a model",
        )

    base = node.agent_url.rstrip("/")
    headers = {AGENT_TOKEN_HEADER: node.agent_token}
    try:
        async with httpx.AsyncClient(timeout=MODEL_PULL_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{base}/models/pull", json={"model": body.model}, headers=headers
            )
            response.raise_for_status()
            result = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Agent on {node.name!r} unreachable or failed: {exc}",
        ) from exc
    if result.get("status") != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Agent could not pull {body.model!r}: {result.get('error', 'unknown error')}",
        )

    # refresh the node's installed-model inventory so recommendations and the
    # scheduler see the new model immediately (not just at next registration)
    models = list(result.get("models", []))
    profile = dict(node.hardware_profile or {})
    runtimes = [dict(r) for r in profile.get("runtimes", [])]
    for runtime in runtimes:
        if runtime.get("name", "").lower() == "ollama":
            runtime["models"] = models
            break
    else:
        runtimes.append({"name": "ollama", "models": models})
    profile["runtimes"] = runtimes
    node.hardware_profile = profile
    flag_modified(node, "hardware_profile")
    await audit(
        db,
        action="node.model.install",
        actor_user_id=principal.id if principal.type == "user" else None,
        resource_type="node",
        resource_id=str(node.id),
        detail={"model": body.model},
        ip_address=_client_ip(request),
    )
    await db.commit()
    return ModelInstallResponse(status="succeeded", models=models)


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


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: uuid.UUID,
    principal: AdminDep,
    request: Request,
    db: DbDep,
) -> None:
    """Remove a node from the fabric (admin only). Destructive — the node's
    task-execution history goes with it — hence the admin gate. Audited."""
    node = await node_service.get_node(db, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    actor_user_id = principal.id if principal.type == "user" else None
    await node_service.delete_node(
        db, node, actor_user_id=actor_user_id, ip_address=_client_ip(request)
    )
