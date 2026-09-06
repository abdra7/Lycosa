"""Virtual HTTP API acceptance: real ASGI/auth/DB, mocked outbound agent only."""

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.api.v1 import admin
from app.core.config import get_settings
from app.services import orchestrator
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login, make_node


@pytest.mark.parametrize("scenario", ["success", "private", "missing_key", "upstream_error"])
async def test_virtual_cloud_api(client, db_session, users, monkeypatch, scenario):
    key = "virtual-key-not-a-real-credential"
    node = await make_node(
        db_session, "virtual-cloud", role="hybrid", agent_url="https://virtual:8010"
    )
    node.last_heartbeat_at = datetime.now(UTC)
    node.hardware_profile = {"extra": {"cloud_execution_capability": True}}
    await db_session.commit()
    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_models", ["virtual-model"])
    monkeypatch.setattr(settings, "cloud_allowed_node_ids", [str(node.id)])
    monkeypatch.setattr(settings, "cloud_node_origins", {str(node.id): node.agent_url})
    monkeypatch.setattr(
        orchestrator, "provider_key", lambda _: None if scenario == "missing_key" else key
    )
    token = await login(client, ADMIN_EMAIL)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://virtual:8010/execute").mock(
            return_value=httpx.Response(
                502 if scenario == "upstream_error" else 200,
                json={"status": "succeeded", "output": f"virtual response {key}"},
            )
        )
        response = await client.post(
            "/api/v1/tasks",
            headers=bearer(token),
            json={
                "prompt": "write a function",
                "provider": "anthropic",
                "model": "virtual-model",
                "requires_privacy": scenario == "private",
                "max_tokens": 32,
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == ("succeeded" if scenario == "success" else "failed")
        assert key not in response.text
        assert route.call_count == (0 if scenario in {"private", "missing_key"} else 1)
        if route.called:
            assert route.calls.last.request.headers["X-Provider-Credential"] == key
            assert key.encode() not in route.calls.last.request.content
        fetched = await client.get(f"/api/v1/tasks/{body['id']}", headers=bearer(token))
        assert fetched.status_code == 200
        assert fetched.json()["status"] == body["status"]
        assert key not in fetched.text


async def test_provider_api_access_and_redaction(client, users, monkeypatch):
    key = "virtual-secret-never-returned"
    monkeypatch.setattr(admin, "provider_key", lambda _: key)
    assert (await client.get("/api/v1/admin/providers")).status_code == 401
    operator = await login(client, OPERATOR_EMAIL)
    assert (
        await client.get("/api/v1/admin/providers", headers=bearer(operator))
    ).status_code == 403
    token = await login(client, ADMIN_EMAIL)
    response = await client.get("/api/v1/admin/providers", headers=bearer(token))
    assert response.status_code == 200
    assert key not in response.text
    cloud = next(item for item in response.json() if item["name"] == "anthropic")
    assert cloud["credential_configured"] is True


@pytest.mark.parametrize(
    "override", [{"provider": "openai"}, {"max_tokens": 0}, {"temperature": 2}]
)
async def test_virtual_api_invalid_request(client, users, override):
    token = await login(client, ADMIN_EMAIL)
    with respx.mock:
        response = await client.post(
            "/api/v1/tasks", headers=bearer(token), json={"prompt": "hello", **override}
        )
    assert response.status_code == 422
