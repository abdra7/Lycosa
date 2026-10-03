"""OpenRouter OAuth PKCE: loopback-only callback, S256 challenge, sealed
user-bound flow, server-side code exchange, key stored like any other."""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from sqlalchemy import select

from app.llm import oauth
from app.llm.vault import get_cipher
from app.models import LLMProviderAccount
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login

EXCHANGE = "https://openrouter.ai/api/v1/auth/keys"
ISSUED = "sk-or-v1-issued-by-oauth"


@pytest.fixture
async def operator(client, users):
    return bearer(await login(client, OPERATOR_EMAIL))


async def start(client, headers, callback="http://127.0.0.1:53111/callback"):
    body = {} if callback is None else {"callback_url": callback}
    return await client.post("/api/v1/llm/oauth/openrouter/start", headers=headers, json=body)


@pytest.mark.parametrize(
    "callback",
    [
        "https://attacker.example/cb",
        "http://192.168.1.5:5000/cb",
        "http://127.0.0.1/cb",  # no port
        "http://u:p@127.0.0.1:5000/cb",
        "http://127.0.0.1:5000/cb?x=1",
    ],
)
async def test_callback_must_be_a_local_loopback_listener(client, operator, callback):
    assert (await start(client, operator, callback)).status_code == 422


async def test_authorization_url_uses_s256_pkce(client, operator):
    response = await start(client, operator)
    assert response.status_code == 200
    body = response.json()
    url = urlsplit(body["authorization_url"])
    params = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://openrouter.ai/auth"
    assert params["callback_url"] == ["http://127.0.0.1:53111/callback"]
    assert params["code_challenge_method"] == ["S256"]
    flow = json.loads(get_cipher().unseal(body["flow"], oauth._PURPOSE))
    digest = hashlib.sha256(flow["v"].encode()).digest()
    assert params["code_challenge"] == [base64.urlsafe_b64encode(digest).decode().rstrip("=")]
    assert flow["v"] not in body["authorization_url"]  # the verifier never leaves


async def test_manual_code_mode_omits_the_callback(client, operator):
    body = (await start(client, operator, callback=None)).json()
    assert "callback_url" not in parse_qs(urlsplit(body["authorization_url"]).query)


@respx.mock
async def test_complete_exchanges_server_side_and_stores_the_key(client, operator, db_session):
    flow = (await start(client, operator)).json()["flow"]
    route = respx.post(EXCHANGE).mock(return_value=httpx.Response(200, json={"key": ISSUED}))
    response = await client.post(
        "/api/v1/llm/oauth/openrouter/complete",
        headers=operator,
        json={"flow": flow, "code": "auth-code-123", "label": "My OpenRouter"},
    )
    assert response.status_code == 201, response.text
    assert ISSUED not in response.text
    body = response.json()
    assert body["provider"] == "openrouter" and body["auth_method"] == "oauth_pkce"
    assert body["credential_configured"] is True

    sent = json.loads(route.calls.last.request.content)
    assert sent["code"] == "auth-code-123" and sent["code_challenge_method"] == "S256"
    verifier = json.loads(get_cipher().unseal(flow, oauth._PURPOSE))["v"]
    assert sent["code_verifier"] == verifier
    account = (await db_session.execute(select(LLMProviderAccount))).scalar_one()
    from app.llm import vault

    assert await vault.load_credential(db_session, account) == ISSUED


@respx.mock
async def test_flows_are_bound_to_the_user_who_started_them(client, users, operator):
    flow = (await start(client, operator)).json()["flow"]
    respx.post(EXCHANGE).mock(return_value=httpx.Response(200, json={"key": ISSUED}))
    admin = bearer(await login(client, ADMIN_EMAIL))
    response = await client.post(
        "/api/v1/llm/oauth/openrouter/complete",
        headers=admin,
        json={"flow": flow, "code": "c"},
    )
    assert response.status_code == 422
    assert not respx.calls  # no exchange attempted with a stolen flow


async def test_expired_and_tampered_flows_are_refused(client, operator, monkeypatch):
    flow = (await start(client, operator)).json()["flow"]
    for bad in (flow[:-4] + "AAAA", "not-a-flow"):
        response = await client.post(
            "/api/v1/llm/oauth/openrouter/complete",
            headers=operator,
            json={"flow": bad, "code": "c"},
        )
        assert response.status_code == 422
    real_time = time.time
    monkeypatch.setattr(oauth.time, "time", lambda: real_time() + oauth.FLOW_TTL_SECONDS + 5)
    expired = await client.post(
        "/api/v1/llm/oauth/openrouter/complete",
        headers=operator,
        json={"flow": flow, "code": "c"},
    )
    assert expired.status_code == 422 and "expired" in expired.json()["error"]["message"]


@respx.mock
async def test_rejected_code_is_reported_without_provider_text(client, operator):
    flow = (await start(client, operator)).json()["flow"]
    respx.post(EXCHANGE).mock(
        return_value=httpx.Response(400, json={"error": {"message": "SECRET-DETAIL"}})
    )
    response = await client.post(
        "/api/v1/llm/oauth/openrouter/complete",
        headers=operator,
        json={"flow": flow, "code": "c"},
    )
    assert response.status_code == 422 and "SECRET-DETAIL" not in response.text


async def test_api_key_principals_cannot_sign_in(client, node_api_key):
    response = await client.post(
        "/api/v1/llm/oauth/openrouter/start", headers={"X-API-Key": node_api_key[0]}, json={}
    )
    assert response.status_code == 403
