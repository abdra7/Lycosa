from datetime import UTC, datetime

import httpx
import respx
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuditLog
from app.schemas.task import TaskCreate
from app.services import orchestrator, provider_secrets
from tests.conftest import make_node


@respx.mock
async def test_cloud_task_no_secret_in_persisted_result_or_audit(db_session, monkeypatch):
    key = "test-key-never-persist"
    node = await make_node(db_session, "cloud", role="hybrid", agent_url="https://cloud:8010")
    node.last_heartbeat_at = datetime.now(UTC)
    node.hardware_profile = {"extra": {"cloud_execution_capability": True}}
    await db_session.commit()
    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_models", ["model"])
    monkeypatch.setattr(settings, "cloud_allowed_node_ids", [str(node.id)])
    monkeypatch.setattr(settings, "cloud_node_origins", {str(node.id): node.agent_url})
    monkeypatch.setattr(orchestrator, "provider_key", lambda _: key)
    route = respx.post("https://cloud:8010/execute").mock(
        return_value=httpx.Response(200, json={"status": "succeeded", "output": f"result {key}"})
    )
    task = await orchestrator.submit_task(
        db_session, TaskCreate(prompt="hello", provider="anthropic", model="model"), None, None
    )
    assert task.status == "succeeded"
    assert key not in str(task.payload) + str(task.result)
    assert task.result["routing"]["execution_mode"] == "cloud"
    assert route.calls.last.request.headers["X-Provider-Credential"] == key
    assert key.encode() not in route.calls.last.request.content
    audits = (await db_session.execute(select(AuditLog))).scalars().all()
    assert key not in str([a.detail for a in audits])


def test_vault_failure_is_sanitized(monkeypatch):
    class Broken:
        def get_password(self, *args):
            raise RuntimeError("sensitive details")

    monkeypatch.setattr(provider_secrets, "_vault", lambda: Broken())
    import pytest

    with pytest.raises(provider_secrets.SecretStoreUnavailable) as error:
        provider_secrets.provider_key("anthropic")
    assert "sensitive" not in str(error.value)
