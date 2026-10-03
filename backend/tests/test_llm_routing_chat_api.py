"""/api/v1/llm routing, chat (JSON and SSE), test prompt and usage."""

import json

import httpx
import pytest
import respx
from sqlalchemy import select

from app.llm import discovery, gateway
from app.models import LLMUsage
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login
from tests.llm_helpers import TEST_KEY, anthropic_message, make_account, openai_completion

OPENAI = "https://api.openai.com/v1/chat/completions"
ANTHROPIC = "https://api.anthropic.com/v1/messages"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(gateway, "sleep", no_sleep)
    discovery.invalidate()


@pytest.fixture
async def tokens(client, users):
    return {
        "admin": bearer(await login(client, ADMIN_EMAIL)),
        "operator": bearer(await login(client, OPERATOR_EMAIL)),
    }


def chain(*entries):
    return {"chain": [{"account_id": str(a.id), "model": m} for a, m in entries]}


# --- routing --------------------------------------------------------------------


async def test_personal_and_deployment_routes(client, tokens, users, db_session):
    shared = await make_account(db_session, "openai", label="org")
    mine = await make_account(db_session, "anthropic", owner=users["operator"].id)
    admins = await make_account(db_session, "mistral", owner=users["admin"].id)

    put = await client.put(
        "/api/v1/llm/routing/default",
        headers=tokens["operator"],
        json=chain((mine, "c"), (shared, "g")),
    )
    assert put.status_code == 200 and put.json()["scope"] == "personal"
    # someone else's personal account can never enter your route
    stolen = await client.put(
        "/api/v1/llm/routing/coding", headers=tokens["operator"], json=chain((admins, "m"))
    )
    assert stolen.status_code == 422
    # operators cannot write deployment routes
    shared_route = {"scope": "deployment", **chain((shared, "g"))}
    assert (
        await client.put(
            "/api/v1/llm/routing/default", headers=tokens["operator"], json=shared_route
        )
    ).status_code == 403
    # a deployment route may not carry a personal key, even the admin's own
    leaky = {"scope": "deployment", **chain((admins, "m"))}
    assert (
        await client.put("/api/v1/llm/routing/default", headers=tokens["admin"], json=leaky)
    ).status_code == 422
    assert (
        await client.put("/api/v1/llm/routing/default", headers=tokens["admin"], json=shared_route)
    ).status_code == 200

    overview = (await client.get("/api/v1/llm/routing", headers=tokens["operator"])).json()
    assert overview["purposes"][0] == "default"
    assert [p["purpose"] for p in overview["personal"]] == ["default"]
    assert len(overview["personal"][0]["chain"]) == 2
    assert overview["deployment"][0]["chain"] == [{"account_id": str(shared.id), "model": "g"}]

    assert (
        await client.delete("/api/v1/llm/routing/default", headers=tokens["operator"])
    ).status_code == 204
    assert (
        await client.delete("/api/v1/llm/routing/default", headers=tokens["operator"])
    ).status_code == 404


async def test_private_routes_accept_only_local_runtimes(client, tokens, db_session):
    cloud = await make_account(db_session, "openai")
    local = await make_account(
        db_session, "ollama", base_url="http://127.0.0.1:11434", key=None, is_local=True
    )
    bad = await client.put(
        "/api/v1/llm/routing/private", headers=tokens["operator"], json=chain((cloud, "x"))
    )
    assert bad.status_code == 422
    ok = await client.put(
        "/api/v1/llm/routing/private", headers=tokens["operator"], json=chain((local, "llama"))
    )
    assert ok.status_code == 200


async def test_unknown_purpose_and_missing_route(client, tokens):
    assert (
        await client.put(
            "/api/v1/llm/routing/fastest", headers=tokens["operator"], json={"chain": []}
        )
    ).status_code == 422
    missing = await client.post(
        "/api/v1/llm/test", headers=tokens["operator"], json={"prompt": "hi"}
    )
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "no_llm_route"


# --- test prompt / chat ------------------------------------------------------------


@respx.mock
async def test_route_with_fallback_through_the_test_endpoint(client, tokens, users, db_session):
    primary = await make_account(db_session, "openai", owner=users["operator"].id)
    backup = await make_account(db_session, "anthropic", owner=users["operator"].id)
    await client.put(
        "/api/v1/llm/routing/default",
        headers=tokens["operator"],
        json=chain((primary, "gpt-x"), (backup, "claude-x")),
    )
    respx.post(OPENAI).mock(return_value=httpx.Response(500))
    respx.post(ANTHROPIC).mock(
        return_value=httpx.Response(200, json=anthropic_message("fallback ok"))
    )
    response = await client.post(
        "/api/v1/llm/test", headers=tokens["operator"], json={"prompt": "hello"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content"] == "fallback ok" and body["provider"] == "anthropic"
    assert body["fallback_index"] == 1 and body["account_id"] == str(backup.id)
    assert TEST_KEY not in response.text


@respx.mock
async def test_chat_with_tools_returns_calls_without_running_them(
    client, tokens, users, db_session
):
    account = await make_account(db_session, "openai", owner=users["operator"].id)
    respx.post(OPENAI).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {"name": "lookup", "arguments": '{"q":"x"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )
    )
    response = await client.post(
        "/api/v1/llm/chat",
        headers=tokens["operator"],
        json={
            "account_id": str(account.id),
            "model": "gpt-x",
            "messages": [{"role": "user", "content": "find x"}],
            "tools": [{"name": "lookup", "parameters": {"type": "object"}}],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["finish_reason"] == "tool_calls"
    assert body["tool_calls"] == [
        {"id": "c1", "name": "lookup", "arguments": {"q": "x"}, "extra": {}}
    ]


async def test_chat_rejects_inconsistent_targets_and_requests(client, tokens, users, db_session):
    account = await make_account(db_session, "openai", owner=users["operator"].id)
    base = {"messages": [{"role": "user", "content": "x"}]}
    for bad in (
        {"account_id": str(account.id)},  # model missing
        {"account_id": str(account.id), "model": "m", "purpose": "coding"},
        {"purpose": "fastest"},
    ):
        response = await client.post(
            "/api/v1/llm/chat", headers=tokens["operator"], json=base | bad
        )
        assert response.status_code == 422, bad
    no_tools = base | {
        "account_id": str(account.id),
        "model": "m",
        "tool_choice": {"mode": "required"},
    }
    assert (
        await client.post("/api/v1/llm/chat", headers=tokens["operator"], json=no_tools)
    ).status_code == 422


async def test_chat_cannot_target_another_users_account(client, tokens, users, db_session):
    admins = await make_account(db_session, "openai", owner=users["admin"].id)
    response = await client.post(
        "/api/v1/llm/chat",
        headers=tokens["operator"],
        json={
            "account_id": str(admins.id),
            "model": "m",
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert response.status_code == 404


@respx.mock
async def test_chat_streams_normalized_sse_events(client, tokens, users, db_session):
    account = await make_account(db_session, "openai", owner=users["operator"].id)
    respx.post(OPENAI).mock(
        return_value=httpx.Response(
            200,
            content=(
                b'data: {"choices":[{"index":0,"delta":{"content":"Hel"}}]}\n\n'
                b'data: {"choices":[{"index":0,"delta":{"content":"lo"},'
                b'"finish_reason":"stop"}]}\n\n'
                b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":2}}\n\n'
                b"data: [DONE]\n\n"
            ),
        )
    )
    async with client.stream(
        "POST",
        "/api/v1/llm/chat",
        headers=tokens["operator"],
        json={
            "account_id": str(account.id),
            "model": "gpt-x",
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
        },
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        raw = (await response.aread()).decode()
    frames = [f for f in raw.split("\n\n") if f]
    events = [f.split("\n")[0].removeprefix("event: ") for f in frames]
    assert events == ["start", "text", "text", "usage", "finish", "done"]
    text = "".join(json.loads(f.split("data: ", 1)[1]).get("text", "") for f in frames[1:3])
    assert text == "Hello"
    db_session.expire_all()
    row = (await db_session.execute(select(LLMUsage))).scalar_one()
    assert row.stream is True and row.status == "succeeded" and row.total_tokens == 4


@respx.mock
async def test_stream_errors_arrive_as_an_error_event(client, tokens, users, db_session):
    account = await make_account(db_session, "openai", owner=users["operator"].id)
    respx.post(OPENAI).mock(return_value=httpx.Response(401))
    async with client.stream(
        "POST",
        "/api/v1/llm/chat",
        headers=tokens["operator"],
        json={
            "account_id": str(account.id),
            "model": "m",
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
        },
    ) as response:
        raw = (await response.aread()).decode()
    assert raw.startswith("event: error")
    assert json.loads(raw.split("data: ", 1)[1])["code"] == "provider_auth_failed"


# --- usage -------------------------------------------------------------------------


@respx.mock
async def test_usage_is_visible_to_its_owner_and_admins_only(client, tokens, users, db_session):
    mine = await make_account(db_session, "openai", owner=users["operator"].id)
    respx.post(OPENAI).mock(return_value=httpx.Response(200, json=openai_completion()))
    await client.post(
        "/api/v1/llm/test",
        headers=tokens["operator"],
        json={"account_id": str(mine.id), "model": "m", "prompt": "x"},
    )
    own = (await client.get("/api/v1/llm/usage", headers=tokens["operator"])).json()
    assert len(own) == 1 and own[0]["provider"] == "openai" and own[0]["total_tokens"] == 5
    assert (await client.get("/api/v1/llm/usage", headers=tokens["admin"])).json() == []
    everyone = (
        await client.get("/api/v1/llm/usage?all_users=true", headers=tokens["admin"])
    ).json()
    assert len(everyone) == 1
    sneaky = (
        await client.get("/api/v1/llm/usage?all_users=true", headers=tokens["operator"])
    ).json()
    assert len(sneaky) == 1  # the flag is ignored for non-admins: still only their own
