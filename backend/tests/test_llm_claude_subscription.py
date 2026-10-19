"""Claude subscription provider (ADR-032): personal accounts bound to a node
whose own signed-in Claude Code runs the call; the controller holds no
Claude credential and names the Lycosa user on every call."""

import json
import uuid

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.security import generate_api_key
from app.llm import discovery, gateway, routing
from app.llm.accounts import connection_for
from app.models import ApiKey, AuditLog, LLMCredential, LLMProviderAccount, LLMUsage, Role
from app.models.user import ROLE_OPERATOR
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login, make_node

AGENT = "http://claude-node:8010"
AGENT_TOKEN = "test-agent-token-0123456789abcdef"
COMPLETE = f"{AGENT}/claude/complete"
STATUS = f"{AGENT}/claude/status"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(gateway, "sleep", no_sleep)
    discovery.invalidate()
    yield
    discovery.invalidate()


async def service_key(db_session) -> dict[str, str]:
    """An operator API key: a service caller, not a signed-in user."""
    role = (await db_session.execute(select(Role).where(Role.name == ROLE_OPERATOR))).scalar_one()
    full_key, prefix, key_hash = generate_api_key()
    db_session.add(ApiKey(key_prefix=prefix, key_hash=key_hash, name="svc", role_id=role.id))
    await db_session.commit()
    return {"X-API-Key": full_key}


@pytest.fixture
async def tokens(client, users):
    return {
        "admin": bearer(await login(client, ADMIN_EMAIL)),
        "operator": bearer(await login(client, OPERATOR_EMAIL)),
    }


async def claude_node(db_session, name: str = "claude-node", *, offers: bool = True):
    node = await make_node(db_session, name)
    node.hardware_profile = {
        **(node.hardware_profile or {}),
        "extra": {"agent_capabilities": {"claude_subscription": offers}},
    }
    await db_session.commit()
    return node


async def connect(client, headers, node_id, **body):
    payload = {
        "provider": "claude_subscription",
        "label": "My Claude",
        "node_id": str(node_id),
        **body,
    }
    return await client.post("/api/v1/llm/accounts", headers=headers, json=payload)


def ndjson(*events: dict) -> bytes:
    return "".join(json.dumps(e) + "\n" for e in events).encode()


HELLO = ndjson(
    {"type": "start", "model": "claude-sonnet-5-5"},
    {"type": "text", "text": "Hel"},
    {"type": "text", "text": "lo"},
    {"type": "usage", "input_tokens": 12, "output_tokens": 3},
    {"type": "finish", "reason": "stop", "model": "claude-sonnet-5-5"},
)


# --- catalogue and account rules ---------------------------------------------------


async def test_provider_is_listed_as_node_run_without_credentials(client, tokens):
    response = await client.get(
        "/api/v1/llm/providers/claude_subscription", headers=tokens["operator"]
    )
    assert response.status_code == 200
    spec = response.json()
    assert spec["kind"] == "node" and spec["auth_methods"] == ["node_sign_in"]
    assert spec["credential_required"] is False
    assert spec["capabilities"]["tool_calling"] is False
    assert any("never sees your Claude credentials" in n for n in spec["notes"])


async def test_connect_binds_a_personal_account_to_the_node(client, tokens, db_session):
    node = await claude_node(db_session)
    response = await connect(client, tokens["operator"], node.id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scope"] == "personal" and body["mine"] and body["usable"]
    assert body["auth_method"] == "node_sign_in"
    assert body["credential_configured"] is False
    assert body["node_id"] == str(node.id) and body["node_name"] == "claude-node"
    assert body["base_url"] == AGENT and body["is_local"] is False
    assert (await db_session.execute(select(LLMCredential))).first() is None
    audit_row = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "llm.account.create"))
    ).scalar_one()
    assert audit_row.detail["node_id"] == str(node.id)


async def test_deployment_scope_is_refused(client, tokens, db_session):
    node = await claude_node(db_session)
    response = await connect(client, tokens["admin"], node.id, scope="deployment")
    assert response.status_code == 422
    assert "personal" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"api_key": "sk-ant-not-wanted-0123456789"}, "api_key"),
        ({"base_url": "http://elsewhere:8010"}, "endpoint"),
    ],
)
async def test_keys_and_endpoints_are_refused(client, tokens, db_session, body, fragment):
    node = await claude_node(db_session)
    response = await connect(client, tokens["operator"], node.id, **body)
    assert response.status_code == 422
    assert fragment in response.json()["error"]["message"]


async def test_node_must_offer_the_runtime(client, tokens, db_session):
    node = await claude_node(db_session, offers=False)
    response = await connect(client, tokens["operator"], node.id)
    assert response.status_code == 422
    assert "LYCOSA_CLAUDE_SUBSCRIPTION" in response.json()["error"]["message"]
    missing = await client.post(
        "/api/v1/llm/accounts",
        headers=tokens["operator"],
        json={"provider": "claude_subscription", "label": "x"},
    )
    assert missing.status_code == 422


async def test_api_key_callers_cannot_own_one(client, db_session, users):
    node = await claude_node(db_session)
    response = await connect(client, await service_key(db_session), node.id)
    assert response.status_code == 403


async def test_other_providers_take_no_node(client, tokens, db_session):
    node = await claude_node(db_session)
    response = await client.post(
        "/api/v1/llm/accounts",
        headers=tokens["operator"],
        json={
            "provider": "openai",
            "label": "x",
            "api_key": "sk-test-account-secret-abcdef",
            "node_id": str(node.id),
        },
    )
    assert response.status_code == 422


async def test_endpoint_and_key_cannot_be_changed_later(client, tokens, db_session):
    node = await claude_node(db_session)
    account_id = (await connect(client, tokens["operator"], node.id)).json()["id"]
    for patch in ({"base_url": "http://elsewhere:8010"}, {"api_key": "sk-ant-zzzzzzzzzzzzzzzz"}):
        response = await client.patch(
            f"/api/v1/llm/accounts/{account_id}", headers=tokens["operator"], json=patch
        )
        assert response.status_code == 422


# --- calls ---------------------------------------------------------------------------


@respx.mock
async def test_chat_runs_on_the_node_for_the_owner(client, tokens, db_session):
    node = await claude_node(db_session)
    account_id = (await connect(client, tokens["operator"], node.id)).json()["id"]
    route = respx.post(COMPLETE).mock(return_value=httpx.Response(200, content=HELLO))
    response = await client.post(
        "/api/v1/llm/chat",
        headers=tokens["operator"],
        json={
            "account_id": account_id,
            "model": "sonnet",
            "system": "Be brief.",
            "messages": [{"role": "user", "content": "Say hello"}],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content"] == "Hello"
    request = route.calls.last.request
    assert request.headers["X-Agent-Token"] == AGENT_TOKEN
    assert request.headers["X-Lycosa-Owner"] == OPERATOR_EMAIL
    sent = json.loads(request.content)
    assert sent == {
        "model": "sonnet",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Say hello"},
        ],
    }
    usage = (await db_session.execute(select(LLMUsage))).scalar_one()
    assert usage.provider == "claude_subscription" and usage.status == "succeeded"
    assert usage.prompt_tokens == 12 and usage.completion_tokens == 3


@respx.mock
async def test_streaming_chat_relays_text(client, tokens, db_session):
    node = await claude_node(db_session)
    account_id = (await connect(client, tokens["operator"], node.id)).json()["id"]
    respx.post(COMPLETE).mock(return_value=httpx.Response(200, content=HELLO))
    response = await client.post(
        "/api/v1/llm/chat",
        headers=tokens["operator"],
        json={
            "account_id": account_id,
            "model": "haiku",
            "stream": True,
            "messages": [{"role": "user", "content": "Say hello"}],
        },
    )
    assert response.status_code == 200
    assert '"Hel"' in response.text and '"lo"' in response.text


async def test_admin_cannot_use_someone_elses_plan(client, tokens, db_session):
    node = await claude_node(db_session)
    account_id = (await connect(client, tokens["operator"], node.id)).json()["id"]
    response = await client.post(
        "/api/v1/llm/chat",
        headers=tokens["admin"],
        json={
            "account_id": account_id,
            "model": "sonnet",
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert response.status_code in (403, 404)


@respx.mock
@pytest.mark.parametrize(
    ("event", "code", "status"),
    [
        ({"type": "error", "code": "not_signed_in"}, "provider_auth_failed", 502),
        ({"type": "error", "code": "usage_limit"}, "provider_quota_exceeded", 502),
        ({"type": "error", "code": "model_unavailable"}, "model_not_found", 422),
    ],
)
async def test_agent_errors_are_normalized(client, tokens, db_session, event, code, status):
    node = await claude_node(db_session)
    account_id = (await connect(client, tokens["operator"], node.id)).json()["id"]
    respx.post(COMPLETE).mock(return_value=httpx.Response(200, content=ndjson(event)))
    response = await client.post(
        "/api/v1/llm/chat",
        headers=tokens["operator"],
        json={
            "account_id": account_id,
            "model": "opus",
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert response.status_code == status, response.text
    assert code in response.text


@respx.mock
async def test_node_refusing_the_user_is_a_permission_error(client, tokens, db_session):
    node = await claude_node(db_session)
    account_id = (await connect(client, tokens["operator"], node.id)).json()["id"]
    respx.post(COMPLETE).mock(
        return_value=httpx.Response(403, json={"detail": "serves a different user"})
    )
    response = await client.post(
        "/api/v1/llm/chat",
        headers=tokens["operator"],
        json={
            "account_id": account_id,
            "model": "sonnet",
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert response.status_code == 502
    assert "different Lycosa user" in response.text


@respx.mock
async def test_task_runs_through_the_account(client, tokens, db_session):
    node = await claude_node(db_session)
    account_id = (await connect(client, tokens["operator"], node.id)).json()["id"]
    respx.post(COMPLETE).mock(return_value=httpx.Response(200, content=HELLO))
    response = await client.post(
        "/api/v1/tasks",
        headers=tokens["operator"],
        json={"prompt": "Say hello", "llm_account_id": account_id, "model": "sonnet"},
    )
    assert response.status_code in (200, 201), response.text
    task = response.json()
    assert task["status"] == "succeeded", task
    result = task["result"]
    assert result["output"] == "Hello"
    assert result["node"] == str(node.id)
    assert result["routing"]["executor"] == "node"
    assert result["routing"]["execution_mode"] == "cloud"


async def test_never_a_private_or_offline_target(client, tokens, db_session, users):
    node = await claude_node(db_session)
    account_id = (await connect(client, tokens["operator"], node.id)).json()["id"]
    account = await db_session.get(LLMProviderAccount, uuid.UUID(account_id))
    assert routing.is_local_target(account) is False
    put = await client.put(
        "/api/v1/llm/routing/private",
        headers=tokens["operator"],
        json={"chain": [{"account_id": account_id, "model": "sonnet"}]},
    )
    assert put.status_code == 422


async def test_connection_uses_the_nodes_current_agent(db_session, users):
    node = await claude_node(db_session)
    account = LLMProviderAccount(
        owner_user_id=users[ROLE_OPERATOR].id,
        provider="claude_subscription",
        label="x",
        auth_method="node_sign_in",
        base_url=AGENT,
        node_id=node.id,
    )
    db_session.add(account)
    await db_session.commit()
    node.agent_url = "http://claude-node-moved:8010"
    node.agent_token = "rotated-agent-token-0123456789abcdef"
    await db_session.commit()
    conn = await connection_for(db_session, account)
    assert conn.base_url == "http://claude-node-moved:8010"
    assert conn.credential == "rotated-agent-token-0123456789abcdef"
    assert conn.headers == {"X-Lycosa-Owner": OPERATOR_EMAIL}
    assert "rotated" not in repr(conn)


async def test_removed_node_reports_unavailable(client, tokens, db_session):
    node = await claude_node(db_session)
    account_id = (await connect(client, tokens["operator"], node.id)).json()["id"]
    account = await db_session.get(LLMProviderAccount, uuid.UUID(account_id))
    account.node_id = None
    await db_session.commit()
    response = await client.post(
        "/api/v1/llm/chat",
        headers=tokens["operator"],
        json={
            "account_id": account_id,
            "model": "sonnet",
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert response.status_code == 503
    assert "node_unavailable" in response.text


# --- health and node listing ---------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    ("status_body", "expected"),
    [
        (
            {
                "enabled": True,
                "installed": True,
                "signed_in": True,
                "owner_matches": True,
                "plan": "pro",
                "version": "2.1.300",
            },
            "ready",
        ),
        (
            {"enabled": True, "installed": True, "signed_in": False, "owner_matches": True},
            "unauthorized",
        ),
        (
            {"enabled": True, "installed": True, "signed_in": True, "owner_matches": False},
            "unauthorized",
        ),
        ({"enabled": True, "installed": False}, "error"),
        ({"enabled": False, "owner_matches": False}, "error"),
    ],
)
async def test_health_reflects_the_node(client, tokens, db_session, status_body, expected):
    node = await claude_node(db_session)
    account_id = (await connect(client, tokens["operator"], node.id)).json()["id"]
    respx.get(STATUS).mock(return_value=httpx.Response(200, json=status_body))
    response = await client.get(
        f"/api/v1/llm/accounts/{account_id}/health", headers=tokens["operator"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == expected


@respx.mock
async def test_subscription_nodes_lists_live_status_for_the_caller(client, tokens, db_session):
    await claude_node(db_session)
    await claude_node(db_session, "plain-node", offers=False)
    route = respx.get(STATUS).mock(
        return_value=httpx.Response(
            200,
            json={
                "enabled": True,
                "installed": True,
                "version": "2.1.300",
                "signed_in": True,
                "auth_method": "subscription",
                "plan": "pro",
                "owner_matches": True,
                "plan_usage": {
                    "status": "allowed",
                    "windows": {"five_hour": {"utilization": 0.07, "resets_at": 1792068000}},
                },
            },
        )
    )
    response = await client.get("/api/v1/llm/claude-subscription/nodes", headers=tokens["operator"])
    assert response.status_code == 200, response.text
    nodes = response.json()
    assert [n["node_name"] for n in nodes] == ["claude-node"]
    entry = nodes[0]
    assert entry["reachable"] and entry["signed_in"] and entry["owner_matches"]
    assert entry["plan"] == "pro"
    assert entry["plan_usage"]["five_hour"]["utilization"] == 0.07
    assert route.calls.last.request.headers["X-Lycosa-Owner"] == OPERATOR_EMAIL


@respx.mock
async def test_subscription_nodes_reports_unreachable_agents(client, tokens, db_session):
    await claude_node(db_session)
    respx.get(STATUS).mock(side_effect=httpx.ConnectError("down"))
    nodes = (
        await client.get("/api/v1/llm/claude-subscription/nodes", headers=tokens["operator"])
    ).json()
    assert nodes[0]["reachable"] is False and nodes[0]["detail"]


async def test_subscription_nodes_needs_a_user(client, db_session, users):
    response = await client.get(
        "/api/v1/llm/claude-subscription/nodes", headers=await service_key(db_session)
    )
    assert response.status_code == 403
