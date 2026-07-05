from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RetrievalRequest
from tests.conftest import ADMIN_EMAIL, bearer, login
from tests.test_knowledge_ingest import create_collection, upload

SPIDER_DOC = b"""Lycosa wolf spiders hunt prey on the ground at night.
Their venom is mild and rarely dangerous to humans.

Wolf spiders carry their egg sacs attached to their spinnerets."""

FLUTTER_DOC = b"""Flutter widgets rebuild when their state changes.
Use StatefulWidget and setState for local widget state management.

The BuildContext locates widgets in the widget tree."""


async def _seed_two_collections(client: AsyncClient, token: str) -> None:
    spiders = await create_collection(client, token, name="spider-facts")
    flutter = await create_collection(client, token, name="flutter-docs")
    await upload(client, token, spiders["id"], "spiders.txt", SPIDER_DOC)
    await upload(client, token, flutter["id"], "flutter.txt", FLUTTER_DOC)


async def test_router_picks_relevant_collection_without_naming_it(
    client: AsyncClient, users: dict, qdrant
) -> None:
    token = await login(client, ADMIN_EMAIL)
    await _seed_two_collections(client, token)

    response = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "flutter widget state management", "top_k": 3},
        headers=bearer(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["chunks"], "expected results"
    # most relevant chunk must come from the flutter collection
    assert body["chunks"][0]["collection"] == "flutter-docs"
    assert "widget" in body["chunks"][0]["text"].lower()
    assert body["context_text"].startswith("[flutter-docs/")
    assert body["latency_ms"] >= 0


async def test_explicit_collection_limits_scope(client: AsyncClient, users: dict, qdrant) -> None:
    token = await login(client, ADMIN_EMAIL)
    await _seed_two_collections(client, token)

    response = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "flutter widget state", "collection": "spider-facts"},
        headers=bearer(token),
    )
    assert response.status_code == 200
    assert all(c["collection"] == "spider-facts" for c in response.json()["chunks"])


async def test_unknown_collection_is_404(client: AsyncClient, users: dict, qdrant) -> None:
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "anything", "collection": "does-not-exist"},
        headers=bearer(token),
    )
    assert response.status_code == 404


async def test_top_k_respected(client: AsyncClient, users: dict, qdrant) -> None:
    token = await login(client, ADMIN_EMAIL)
    await _seed_two_collections(client, token)
    response = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "spiders and widgets", "top_k": 1},
        headers=bearer(token),
    )
    assert len(response.json()["chunks"]) == 1


async def test_retrieval_request_recorded(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    token = await login(client, ADMIN_EMAIL)
    await _seed_two_collections(client, token)
    await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "wolf spider venom"},
        headers=bearer(token),
    )
    record = (
        await db_session.execute(
            select(RetrievalRequest).where(RetrievalRequest.query == "wolf spider venom")
        )
    ).scalar_one()
    assert record.results_count > 0
    assert record.latency_ms >= 0
    assert record.collection_id is None  # federated: router chose, caller didn't
    assert record.requested_by_user_id == users["admin"].id


async def test_retrieve_requires_auth(client: AsyncClient, roles: dict, qdrant) -> None:
    response = await client.post("/api/v1/knowledge/retrieve", json={"query": "x"})
    assert response.status_code == 401


async def test_empty_fabric_returns_empty_results(client: AsyncClient, users: dict, qdrant) -> None:
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/knowledge/retrieve", json={"query": "anything"}, headers=bearer(token)
    )
    assert response.status_code == 200
    assert response.json()["chunks"] == []
