from httpx import AsyncClient

from app.core.security import API_KEY_HEADER
from tests.conftest import ADMIN_EMAIL, bearer, login
from tests.test_nodes_register import PROFILE, payload


async def test_endpoint_requires_auth(client: AsyncClient, roles: dict) -> None:
    response = await client.post("/api/v1/recommendations/node-role", json=PROFILE)
    assert response.status_code == 401


async def test_endpoint_returns_recommendation(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/recommendations/node-role", json=PROFILE, headers=bearer(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["role"] in ("ai_compute", "hybrid", "knowledge", "tool", "vision", "storage")
    assert body["rationale"]
    assert len(body["scores"]) == 6


async def test_registration_stores_recommendation(client: AsyncClient, node_api_key: tuple) -> None:
    full_key, _ = node_api_key
    response = await client.post(
        "/api/v1/nodes/register", json=payload(), headers={API_KEY_HEADER: full_key}
    )
    assert response.status_code == 201
    body = response.json()
    # PROFILE: 32 cores / 64 GB / RTX 4090 (24 GB) / ollama → hybrid territory
    assert body["recommended_role"] == "hybrid"
    assert body["recommendation_confidence"] > 0.5
    assert body["recommendation_rationale"]
    assert body["role"] is None  # assignment stays operator-owned


async def test_override_survives_reregistration(
    client: AsyncClient, node_api_key: tuple, users: dict
) -> None:
    full_key, _ = node_api_key
    headers = {API_KEY_HEADER: full_key}
    created = (await client.post("/api/v1/nodes/register", json=payload(), headers=headers)).json()

    # operator overrides the recommendation
    token = await login(client, ADMIN_EMAIL)
    patched = await client.patch(
        f"/api/v1/nodes/{created['id']}", json={"role": "storage"}, headers=bearer(token)
    )
    assert patched.json()["role"] == "storage"

    # node re-registers (reboot): recommendation recomputed, override untouched
    again = await client.post("/api/v1/nodes/register", json=payload(), headers=headers)
    assert again.status_code == 200
    assert again.json()["role"] == "storage"
    assert again.json()["recommended_role"] == "hybrid"
