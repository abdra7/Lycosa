"""Metrics endpoint, JSON log formatter, and the WebSocket event stream."""

import json
import logging

import respx
from fastapi.testclient import TestClient
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import JsonFormatter, request_id_var, task_id_var
from app.core.security import API_KEY_HEADER
from app.main import app
from tests.conftest import ADMIN_EMAIL, bearer, login, make_node
from tests.test_nodes_register import payload

METRICS = {"cpu_percent": 21.0, "ram_percent": 55.0, "running_tasks": 1}


def test_json_formatter_includes_correlation_ids() -> None:
    request_token = request_id_var.set("req-123")
    task_token = task_id_var.set("task-456")
    try:
        record = logging.LogRecord(
            "lycosa.test", logging.INFO, __file__, 1, "hello %s", ("world",), None
        )
        entry = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(request_token)
        task_id_var.reset(task_token)

    assert entry["message"] == "hello world"
    assert entry["level"] == "info"
    assert entry["request_id"] == "req-123"
    assert entry["task_id"] == "task-456"
    assert "ts" in entry


@respx.mock(assert_all_mocked=False)
async def test_metrics_expose_task_and_node_series(
    respx_mock,
    client: AsyncClient,
    db_session: AsyncSession,
    users: dict,
    node_api_key: tuple,
) -> None:
    # generate signal: a heartbeat and a completed task
    full_key, _ = node_api_key
    await client.post("/api/v1/nodes/register", json=payload(), headers={API_KEY_HEADER: full_key})
    await client.post(
        "/api/v1/nodes/heartbeat",
        json={"metrics": METRICS},
        headers={API_KEY_HEADER: full_key},
    )
    await make_node(db_session, "metrics-box", role="hybrid")
    respx_mock.post("http://metrics-box:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "ok"})
    )
    token = await login(client, ADMIN_EMAIL)
    await client.post("/api/v1/tasks", json={"prompt": "say hello"}, headers=bearer(token))

    response = await client.get("/metrics/")
    body = response.text
    assert response.status_code == 200
    assert "lycosa_tasks_total" in body
    assert 'lycosa_nodes{status="online"}' in body
    assert 'lycosa_node_cpu_percent{node="workstation-01"}' in body
    assert "lycosa_http_requests_total" in body
    assert "lycosa_task_duration_seconds" in body


def _ws_client() -> TestClient:
    return TestClient(app)


async def test_websocket_rejects_unauthenticated(client: AsyncClient, roles: dict) -> None:
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with _ws_client() as tc:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect("/api/v1/events"):
                pass
        assert exc_info.value.code == 4401


async def test_websocket_streams_node_and_alert_events(
    client: AsyncClient, users: dict, node_api_key: tuple, db_session: AsyncSession
) -> None:
    import uuid as uuid_mod
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models import Node
    from app.services.node import sweep_offline_nodes

    token = await login(client, ADMIN_EMAIL)
    full_key, _ = node_api_key
    registered = (
        await client.post(
            "/api/v1/nodes/register", json=payload(), headers={API_KEY_HEADER: full_key}
        )
    ).json()

    with _ws_client() as tc:
        with tc.websocket_connect(f"/api/v1/events?token={token}") as ws:
            # heartbeat -> node.connected + node.metrics.updated
            await client.post(
                "/api/v1/nodes/heartbeat",
                json={"metrics": METRICS},
                headers={API_KEY_HEADER: full_key},
            )
            first = ws.receive_json()
            second = ws.receive_json()
            assert first["type"] == "node.connected"
            assert first["data"]["name"] == "workstation-01"
            assert second["type"] == "node.metrics.updated"
            assert second["data"]["metrics"]["cpu_percent"] == 21.0

            # stale heartbeat + sweep -> node.disconnected + alert.created
            record = (
                await db_session.execute(
                    select(Node).where(Node.id == uuid_mod.UUID(registered["id"]))
                )
            ).scalar_one()
            record.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=600)
            await db_session.commit()
            await sweep_offline_nodes(db_session)

            third = ws.receive_json()
            fourth = ws.receive_json()
            assert third["type"] == "node.disconnected"
            assert fourth["type"] == "alert.created"
            assert "went offline" in fourth["data"]["message"]


@respx.mock(assert_all_mocked=False)
async def test_websocket_streams_workflow_events(
    respx_mock, client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    await make_node(db_session, "wf-box", role="hybrid")
    respx_mock.post("http://wf-box:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "done"})
    )
    token = await login(client, ADMIN_EMAIL)
    created = await client.post(
        "/api/v1/workflows",
        json={
            "name": "events-wf",
            "definition": {"steps": [{"id": "only", "kind": "task", "prompt": "{{input}}"}]},
        },
        headers=bearer(token),
    )

    with _ws_client() as tc:
        with tc.websocket_connect(f"/api/v1/events?token={token}") as ws:
            await client.post(
                f"/api/v1/workflows/{created.json()['id']}/run",
                json={"input": "hello"},
                headers=bearer(token),
            )
            types = [ws.receive_json()["type"] for _ in range(4)]
            assert types[0] == "workflow.started"
            assert "task.started" in types
            assert "workflow.step.completed" in types
            # the last event of a successful run
            assert "workflow.finished" in types or ws.receive_json()["type"] == "workflow.finished"
