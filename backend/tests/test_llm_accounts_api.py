"""/api/v1/llm accounts: connect, list, test, models, disconnect; multi-account,
cross-user isolation, endpoint policy and credential secrecy."""

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import generate_api_key
from app.llm import accounts, discovery
from app.models import ApiKey, AuditLog, LLMCredential, LLMRoutingPolicy, Role
from app.models.user import ROLE_OPERATOR
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login

KEY = "sk-test-account-secret-abcdef"
OPENAI_MODELS = "https://api.openai.com/v1/models"


@pytest.fixture(autouse=True)
def _fresh_model_cache():
    discovery.invalidate()
    yield
    discovery.invalidate()


@pytest.fixture
async def tokens(client, users):
    return {
        "admin": bearer(await login(client, ADMIN_EMAIL)),
        "operator": bearer(await login(client, OPERATOR_EMAIL)),
    }


async def connect(client, headers, **body):
    payload = {"provider": "openai", "label": "Personal", "api_key": KEY, **body}
    return await client.post("/api/v1/llm/accounts", headers=headers, json=payload)


# --- catalogue ----------------------------------------------------------------


async def test_provider_catalogue_requires_an_operator(client, users, tokens, node_api_key):
    assert (await client.get("/api/v1/llm/providers")).status_code == 401
    node = await client.get("/api/v1/llm/providers", headers={"X-API-Key": node_api_key[0]})
    assert node.status_code == 403
    response = await client.get("/api/v1/llm/providers", headers=tokens["operator"])
    assert response.status_code == 200
    ids = {p["id"] for p in response.json()}
    assert {"openai", "anthropic", "gemini", "deepseek", "qwen", "xai", "mistral"} <= ids
    assert {"openrouter", "ollama", "lmstudio", "vllm"} <= ids
    anthropic = (
        await client.get("/api/v1/llm/providers/anthropic", headers=tokens["operator"])
    ).json()
    assert "subscription" in anthropic["subscription_note"]
    assert (
        await client.get("/api/v1/llm/providers/nope", headers=tokens["operator"])
    ).status_code == 404


# --- connect / secrecy ------------------------------------------------------------


async def test_connect_stores_an_encrypted_key_that_is_never_returned(client, tokens, db_session):
    response = await connect(client, tokens["operator"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert KEY not in response.text
    assert body["scope"] == "personal" and body["mine"] and body["usable"]
    assert body["credential_configured"] is True and body["api_access"] == "unknown"
    assert body["base_url"] == "https://api.openai.com/v1" and body["is_local"] is False

    row = (await db_session.execute(select(LLMCredential))).scalar_one()
    assert KEY.encode() not in row.ciphertext
    audit_rows = (await db_session.execute(select(AuditLog))).scalars().all()
    created = [a for a in audit_rows if a.action == "llm.account.create"]
    assert created and KEY not in str([a.detail for a in audit_rows])
    assert created[0].actor_user_id is not None

    listed = await client.get("/api/v1/llm/accounts", headers=tokens["operator"])
    assert KEY not in listed.text and len(listed.json()) == 1


async def test_multiple_accounts_per_provider_and_label_uniqueness(client, tokens):
    assert (await connect(client, tokens["operator"], label="Personal")).status_code == 201
    assert (await connect(client, tokens["operator"], label="Work")).status_code == 201
    duplicate = await connect(client, tokens["operator"], label="Work")
    assert duplicate.status_code == 409
    other_provider = await connect(client, tokens["operator"], provider="deepseek", label="Work")
    assert other_provider.status_code == 201
    labels = sorted(
        (a["provider"], a["label"])
        for a in (await client.get("/api/v1/llm/accounts", headers=tokens["operator"])).json()
    )
    assert labels == [("deepseek", "Work"), ("openai", "Personal"), ("openai", "Work")]


@pytest.mark.parametrize(
    "body,status",
    [
        ({"provider": "nope"}, 422),
        ({"api_key": None}, 422),  # cloud provider without a key
        ({"base_url": "https://evil.example/v1"}, 422),  # fixed official endpoint
        ({"provider": "qwen", "base_url": "https://evil.example/compatible-mode/v1"}, 422),
        ({"label": "bad\nlabel"}, 422),
        ({"api_key": "has space"}, 422),
        ({"scope": "deployment"}, 403),  # operators cannot create shared accounts
    ],
)
async def test_connect_validation(client, tokens, body, status):
    response = await connect(client, tokens["operator"], **body)
    assert response.status_code == status, response.text


async def test_qwen_accepts_a_documented_regional_endpoint(client, tokens):
    response = await connect(
        client,
        tokens["operator"],
        provider="qwen",
        base_url="https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    )
    assert response.status_code == 201, response.text


# --- endpoint policy (SSRF) ----------------------------------------------------


async def test_admin_may_add_a_lan_runtime_without_a_key(client, tokens):
    response = await connect(
        client,
        tokens["admin"],
        provider="ollama",
        label="Lab",
        scope="deployment",
        base_url="http://127.0.0.1:11434",
        api_key=None,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_local"] is True and body["auth_method"] == "none"
    assert body["credential_configured"] is False


@pytest.mark.parametrize(
    "url",
    ["http://169.254.169.254", "http://[fd00:ec2::254]", "http://0.0.0.0:11434", "file:///etc"],
)
async def test_metadata_and_bogus_endpoints_are_refused_even_for_admins(client, tokens, url):
    response = await connect(
        client,
        tokens["admin"],
        provider="ollama",
        label="x",
        scope="deployment",
        base_url=url,
        api_key=None,
    )
    assert response.status_code == 422, response.text


async def test_non_admins_cannot_point_at_private_networks_by_default(client, tokens):
    response = await connect(
        client,
        tokens["operator"],
        provider="lmstudio",
        base_url="http://10.0.0.7:1234/v1",
        api_key=None,
    )
    assert response.status_code == 422
    assert "network policy" in response.json()["error"]["message"]


async def test_admin_can_widen_user_endpoint_networks(client, tokens, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_user_endpoint_networks", "10.0.0.0/8")
    response = await connect(
        client,
        tokens["operator"],
        provider="lmstudio",
        base_url="http://10.0.0.7:1234/v1",
        api_key=None,
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_local"] is True


async def test_dns_names_are_resolved_and_checked(client, tokens, monkeypatch):
    async def resolver(host, port):
        return {"gpu.lan": ["192.168.1.40"], "rebind.example": ["169.254.169.254"]}[host]

    monkeypatch.setattr(accounts, "RESOLVER", resolver)
    ok = await connect(
        client,
        tokens["admin"],
        provider="vllm",
        label="GPU",
        scope="deployment",
        base_url="http://gpu.lan:8000/v1",
        api_key=None,
    )
    assert ok.status_code == 201 and ok.json()["is_local"] is True
    bad = await connect(
        client,
        tokens["admin"],
        provider="vllm",
        label="Bad",
        scope="deployment",
        base_url="http://rebind.example:8000/v1",
        api_key=None,
    )
    assert bad.status_code == 422


async def test_keys_are_never_sent_over_plain_http_to_public_hosts(client, tokens, monkeypatch):
    async def resolver(host, port):
        return ["93.184.216.34"]

    monkeypatch.setattr(accounts, "RESOLVER", resolver)
    response = await connect(
        client,
        tokens["admin"],
        provider="openai_compatible",
        label="Remote",
        scope="deployment",
        base_url="http://llm.example.com/v1",
    )
    assert response.status_code == 422
    assert "HTTPS" in response.json()["error"]["message"]


# --- isolation ---------------------------------------------------------------------


async def test_personal_accounts_are_isolated_between_users(client, tokens, db_session):
    admin_own = (await connect(client, tokens["admin"], label="Admin own")).json()
    operator_own = (await connect(client, tokens["operator"], label="Op own")).json()

    op_view = await client.get("/api/v1/llm/accounts", headers=tokens["operator"])
    assert [a["id"] for a in op_view.json()] == [operator_own["id"]]
    for method, suffix in [("get", ""), ("post", "/test"), ("get", "/models"), ("delete", "")]:
        response = await getattr(client, method)(
            f"/api/v1/llm/accounts/{admin_own['id']}{suffix}", headers=tokens["operator"]
        )
        assert response.status_code == 404, (method, suffix)  # existence not confirmed

    # admins see metadata of personal accounts but can neither use nor re-key them
    admin_view = {
        a["id"]: a
        for a in (await client.get("/api/v1/llm/accounts", headers=tokens["admin"])).json()
    }
    seen = admin_view[operator_own["id"]]
    assert seen["usable"] is False and seen["manageable"] is False and seen["mine"] is False
    url = f"/api/v1/llm/accounts/{operator_own['id']}"
    assert (await client.post(url + "/test", headers=tokens["admin"])).status_code == 403
    assert (await client.get(url + "/models", headers=tokens["admin"])).status_code == 403
    patched = await client.patch(url, headers=tokens["admin"], json={"api_key": "sk-swapped-key"})
    assert patched.status_code == 403
    # ...but may remove one (governance), which is audited
    assert (await client.delete(url, headers=tokens["admin"])).status_code == 204
    actions = [a.action for a in (await db_session.execute(select(AuditLog))).scalars()]
    assert "llm.account.delete" in actions


async def test_deployment_accounts_are_shared_but_admin_managed(client, tokens):
    shared = (await connect(client, tokens["admin"], label="Org", scope="deployment")).json()
    op_view = (await client.get("/api/v1/llm/accounts", headers=tokens["operator"])).json()
    entry = next(a for a in op_view if a["id"] == shared["id"])
    assert entry["usable"] is True and entry["manageable"] is False
    url = f"/api/v1/llm/accounts/{shared['id']}"
    assert (
        await client.patch(url, headers=tokens["operator"], json={"label": "x"})
    ).status_code == 403
    assert (await client.delete(url, headers=tokens["operator"])).status_code == 403


async def test_operator_api_keys_use_shared_accounts_but_own_none(
    client, tokens, db_session, roles
):
    full_key, prefix, key_hash = generate_api_key()
    role = (await db_session.execute(select(Role).where(Role.name == ROLE_OPERATOR))).scalar_one()
    db_session.add(ApiKey(key_prefix=prefix, key_hash=key_hash, name="svc", role_id=role.id))
    await db_session.commit()
    service = {"X-API-Key": full_key}
    await connect(client, tokens["admin"], label="Org", scope="deployment")
    await connect(client, tokens["operator"], label="Mine")
    listed = (await client.get("/api/v1/llm/accounts", headers=service)).json()
    assert [a["label"] for a in listed] == ["Org"]
    assert (await connect(client, service, label="Svc")).status_code == 403


# --- test connection / models / disconnect ----------------------------------------


@respx.mock
async def test_connection_test_reports_real_status_and_api_access(client, tokens):
    account = (await connect(client, tokens["operator"])).json()
    url = f"/api/v1/llm/accounts/{account['id']}/test"
    route = respx.get(OPENAI_MODELS).mock(
        side_effect=[
            httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]}),
            httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}}),
        ]
    )
    ok = await client.post(url, headers=tokens["operator"])
    assert ok.status_code == 200
    assert ok.json() | {"latency_ms": None} == {
        "status": "ready",
        "detail": "",
        "latency_ms": None,
        "models_available": 2,
        "api_access": "available",
        "subscription_note": None,
    }
    assert route.calls.last.request.headers["authorization"] == f"Bearer {KEY}"

    denied = await client.post(url, headers=tokens["operator"])
    body = denied.json()
    assert body["status"] == "unauthorized" and body["api_access"] == "unavailable"
    assert "subscription" in body["subscription_note"]
    assert KEY not in denied.text

    current = (
        await client.get(f"/api/v1/llm/accounts/{account['id']}", headers=tokens["operator"])
    ).json()
    assert current["last_test_status"] == "unauthorized" and current["api_access"] == "unavailable"


@respx.mock
async def test_connection_test_with_a_model_sends_a_minimal_prompt(client, tokens):
    account = (await connect(client, tokens["operator"])).json()
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
        )
    )
    response = await client.post(
        f"/api/v1/llm/accounts/{account['id']}/test",
        headers=tokens["operator"],
        json={"model": "gpt-x"},
    )
    assert response.json()["status"] == "ready"
    sent = route.calls.last.request.read()
    assert b'"max_completion_tokens":16' in sent.replace(b" ", b"")


@respx.mock
async def test_model_listing_and_aggregation_with_a_failing_account(client, tokens):
    good = (await connect(client, tokens["operator"], label="Good")).json()
    bad = (await connect(client, tokens["operator"], provider="mistral", label="Bad")).json()
    respx.get(OPENAI_MODELS).mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-x"}]})
    )
    respx.get("https://api.mistral.ai/v1/models").mock(return_value=httpx.Response(503))

    models = await client.get(
        f"/api/v1/llm/accounts/{good['id']}/models", headers=tokens["operator"]
    )
    (model,) = models.json()["models"]
    assert model["ref"] == "openai:gpt-x" and model["account_label"] == "Good"
    assert model["capabilities"]["tool_calling"] is True  # API-level
    assert model["capabilities"]["vision"] is None  # not documented per model: unknown

    everything = (await client.get("/api/v1/llm/models", headers=tokens["operator"])).json()
    assert [m["ref"] for m in everything["models"]] == ["openai:gpt-x"]
    assert everything["errors"] == [
        {"account_id": bad["id"], "error": "The provider is unavailable or overloaded"}
    ]


@respx.mock
async def test_health_endpoint(client, tokens):
    account = (await connect(client, tokens["operator"])).json()
    respx.get(OPENAI_MODELS).mock(side_effect=httpx.ConnectError("down"))
    health = await client.get(
        f"/api/v1/llm/accounts/{account['id']}/health", headers=tokens["operator"]
    )
    assert health.status_code == 200 and health.json()["status"] == "offline"


async def test_disabled_accounts_cannot_be_used(client, tokens):
    account = (await connect(client, tokens["operator"])).json()
    url = f"/api/v1/llm/accounts/{account['id']}"
    patched = await client.patch(url, headers=tokens["operator"], json={"status": "disabled"})
    assert patched.status_code == 200 and patched.json()["usable"] is False
    assert (await client.post(url + "/test", headers=tokens["operator"])).status_code == 409


async def test_key_rotation_and_disconnect_cleanup(client, tokens, db_session):
    account = (await connect(client, tokens["operator"])).json()
    url = f"/api/v1/llm/accounts/{account['id']}"
    rotated = await client.patch(url, headers=tokens["operator"], json={"api_key": KEY + "-new"})
    assert rotated.status_code == 200 and KEY not in rotated.text
    db_session.add(
        LLMRoutingPolicy(
            owner_user_id=None,
            purpose="default",
            chain=[
                {"account_id": account["id"], "model": "m"},
                {"account_id": "other", "model": "n"},
            ],
        )
    )
    await db_session.commit()
    assert (await client.delete(url, headers=tokens["operator"])).status_code == 204
    db_session.expire_all()
    assert (await db_session.execute(select(LLMCredential))).scalar_one_or_none() is None
    policy = (await db_session.execute(select(LLMRoutingPolicy))).scalar_one()
    assert policy.chain == [{"account_id": "other", "model": "n"}]
    assert (await client.get(url, headers=tokens["operator"])).status_code == 404
