"""Security audit 2026-10 (ADR-030): a node's agent_url must be a bare
http(s) origin, so a node key cannot steer controller requests to arbitrary
paths or internal-only services."""

import pytest
import respx
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agenturl import InvalidAgentUrl, normalize_agent_url
from app.core.security import API_KEY_HEADER
from tests.conftest import ADMIN_EMAIL, bearer, login, make_node
from tests.test_nodes_register import payload

BAD_URLS = [
    "ftp://agent.example.invalid:8010",
    "http://agent.example.invalid:8010/collections/kc_00/snapshots?x=",
    "http://agent.example.invalid:8010/#frag",
    "http://agent.example.invalid:8010/path",
    "http://user:pw@agent.example.invalid:8010",
    "agent.example.invalid:8010",
    "http://:8010",
    "http://agent.example.invalid:99999",
]


@pytest.mark.parametrize("url", BAD_URLS)
def test_normalize_rejects_non_origin_urls(url: str) -> None:
    with pytest.raises(InvalidAgentUrl):
        normalize_agent_url(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://192.168.1.5:8010", "http://192.168.1.5:8010"),
        ("http://192.168.1.5:8010/", "http://192.168.1.5:8010"),
        ("https://Cloud.Example:443", "https://cloud.example:443"),
        ("http://[fe80::1]:8010", "http://[fe80::1]:8010"),
    ],
)
def test_normalize_accepts_bare_origins(url: str, expected: str) -> None:
    assert normalize_agent_url(url) == expected


@pytest.mark.parametrize("url", BAD_URLS)
async def test_register_rejects_non_origin_agent_url(
    client: AsyncClient, node_api_key: tuple, url: str
) -> None:
    body = dict(payload(), agent_url=url, agent_token="t" * 32)
    response = await client.post(
        "/api/v1/nodes/register", json=body, headers={API_KEY_HEADER: node_api_key[0]}
    )
    assert response.status_code == 422


async def test_register_stores_normalized_origin(client: AsyncClient, node_api_key: tuple) -> None:
    body = dict(payload(), agent_url="http://192.168.1.5:8010/", agent_token="t" * 32)
    response = await client.post(
        "/api/v1/nodes/register", json=body, headers={API_KEY_HEADER: node_api_key[0]}
    )
    assert response.status_code == 201, response.text
    assert response.json()["agent_url"] == "http://192.168.1.5:8010"


@respx.mock(assert_all_mocked=False, assert_all_called=False)
async def test_legacy_invalid_agent_url_is_never_dispatched(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict
) -> None:
    """A row stored before validation existed must not receive controller traffic."""
    await make_node(
        db_session,
        "ssrf-box",
        role="ai_compute",
        agent_url="http://qdrant.invalid:6333/collections/kc_00/snapshots?x=",
    )
    route = respx_mock.post(url__startswith="http://qdrant.invalid:6333").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "x"})
    )

    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/tasks", json={"prompt": "Refactor this python function"}, headers=bearer(token)
    )

    assert response.status_code == 201, response.text
    assert not route.called
    assert response.json()["status"] == "failed"
