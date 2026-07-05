import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import API_KEY_HEADER
from app.models import AuditLog, Node
from app.services.node import sweep_offline_nodes
from tests.conftest import OPERATOR_EMAIL, bearer, login
from tests.test_nodes_register import payload

METRICS = {
    "cpu_percent": 23.5,
    "ram_percent": 41.0,
    "ram_used_gb": 26.2,
    "disk_percent": 60.1,
    "gpus": [{"util_percent": 55, "mem_used_gb": 8.1, "temp_c": 61}],
    "running_tasks": 0,
}


async def _register(client: AsyncClient, full_key: str) -> dict:
    response = await client.post(
        "/api/v1/nodes/register", json=payload(), headers={API_KEY_HEADER: full_key}
    )
    assert response.status_code == 201
    return response.json()


async def test_heartbeat_marks_online_and_stores_metrics(
    client: AsyncClient, node_api_key: tuple, users: dict
) -> None:
    full_key, _ = node_api_key
    node = await _register(client, full_key)
    assert node["status"] == "registered"

    response = await client.post(
        "/api/v1/nodes/heartbeat", json={"metrics": METRICS}, headers={API_KEY_HEADER: full_key}
    )
    assert response.status_code == 200, response.text
    assert response.json()["heartbeat_interval_seconds"] > 0

    token = await login(client, OPERATOR_EMAIL)
    fetched = (await client.get(f"/api/v1/nodes/{node['id']}", headers=bearer(token))).json()
    assert fetched["status"] == "online"
    assert fetched["last_heartbeat_at"] is not None
    assert fetched["metrics"]["cpu_percent"] == 23.5
    assert fetched["metrics"]["gpus"][0]["temp_c"] == 61


async def test_heartbeat_with_unbound_key_is_409(client: AsyncClient, node_api_key: tuple) -> None:
    full_key, _ = node_api_key  # never registered
    response = await client.post(
        "/api/v1/nodes/heartbeat", json={"metrics": METRICS}, headers={API_KEY_HEADER: full_key}
    )
    assert response.status_code == 409
    assert "register" in response.json()["error"]["message"]


async def test_heartbeat_unauthenticated_is_401(client: AsyncClient, roles: dict) -> None:
    response = await client.post("/api/v1/nodes/heartbeat", json={"metrics": METRICS})
    assert response.status_code == 401


async def test_online_to_offline_transition_is_audited(
    client: AsyncClient, node_api_key: tuple, db_session: AsyncSession
) -> None:
    full_key, _ = node_api_key
    await _register(client, full_key)
    await client.post(
        "/api/v1/nodes/heartbeat", json={"metrics": METRICS}, headers={API_KEY_HEADER: full_key}
    )
    actions = [row[0] for row in await db_session.execute(select(AuditLog.action))]
    assert "node.online" in actions


async def test_sweep_flips_stale_nodes_offline(
    client: AsyncClient, node_api_key: tuple, db_session: AsyncSession
) -> None:
    full_key, _ = node_api_key
    node = await _register(client, full_key)
    await client.post(
        "/api/v1/nodes/heartbeat", json={"metrics": METRICS}, headers={API_KEY_HEADER: full_key}
    )

    # backdate the heartbeat past the timeout
    record = (
        await db_session.execute(select(Node).where(Node.id == uuid.UUID(node["id"])))
    ).scalar_one()
    record.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=300)
    await db_session.commit()

    flipped = await sweep_offline_nodes(db_session)
    assert flipped == 1

    await db_session.refresh(record)
    assert record.status == "offline"

    actions = [row[0] for row in await db_session.execute(select(AuditLog.action))]
    assert "node.offline" in actions


async def test_sweep_leaves_fresh_nodes_online(
    client: AsyncClient, node_api_key: tuple, db_session: AsyncSession
) -> None:
    full_key, _ = node_api_key
    node = await _register(client, full_key)
    await client.post(
        "/api/v1/nodes/heartbeat", json={"metrics": METRICS}, headers={API_KEY_HEADER: full_key}
    )

    flipped = await sweep_offline_nodes(db_session)
    assert flipped == 0

    record = (
        await db_session.execute(select(Node).where(Node.id == uuid.UUID(node["id"])))
    ).scalar_one()
    assert record.status == "online"


async def test_registration_stores_agent_contact_but_hides_token(
    client: AsyncClient, node_api_key: tuple, db_session: AsyncSession, users: dict
) -> None:
    full_key, _ = node_api_key
    body = payload()
    body["agent_url"] = "http://192.168.1.50:8010"
    body["agent_token"] = "a-very-secret-agent-token-1234"
    response = await client.post(
        "/api/v1/nodes/register", json=body, headers={API_KEY_HEADER: full_key}
    )
    assert response.status_code == 201
    assert response.json()["agent_url"] == "http://192.168.1.50:8010"
    assert "agent_token" not in response.json()

    # stored server-side for the Sprint 5 dispatcher
    record = (
        await db_session.execute(select(Node).where(Node.id == uuid.UUID(response.json()["id"])))
    ).scalar_one()
    assert record.agent_token == "a-very-secret-agent-token-1234"
