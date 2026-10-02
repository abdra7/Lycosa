"""Security audit 2026-10 (ADR-030): knowledge reads are operator-only, and
retrieval queries are bounded and embedded off the event loop."""

import threading

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import API_KEY_HEADER
from app.models import RetrievalRequest
from app.services.knowledge import router as knowledge_router
from app.services.knowledge.router import MAX_QUERY_CHARS, retrieve
from tests.conftest import OPERATOR_EMAIL, bearer, login
from tests.test_knowledge_ingest import MARKDOWN, create_collection, upload


async def _seed(client: AsyncClient) -> tuple[str, dict]:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="private-docs")
    await upload(client, token, collection["id"], "spiders.md", MARKDOWN)
    return token, collection


async def test_node_key_cannot_read_knowledge(
    client: AsyncClient, users: dict, qdrant, node_api_key: tuple
) -> None:
    _, collection = await _seed(client)
    headers = {API_KEY_HEADER: node_api_key[0]}

    retrieved = await client.post(
        "/api/v1/knowledge/retrieve", json={"query": "wolf spiders"}, headers=headers
    )
    collections = await client.get("/api/v1/knowledge/collections", headers=headers)
    documents = await client.get(
        f"/api/v1/knowledge/collections/{collection['id']}/documents", headers=headers
    )

    assert retrieved.status_code == 403
    assert collections.status_code == 403
    assert documents.status_code == 403


async def test_operator_can_still_read_knowledge(client: AsyncClient, users: dict, qdrant) -> None:
    token, collection = await _seed(client)

    retrieved = await client.post(
        "/api/v1/knowledge/retrieve", json={"query": "wolf spiders"}, headers=bearer(token)
    )
    collections = await client.get("/api/v1/knowledge/collections", headers=bearer(token))
    documents = await client.get(
        f"/api/v1/knowledge/collections/{collection['id']}/documents", headers=bearer(token)
    )

    assert retrieved.status_code == 200 and retrieved.json()["chunks"]
    assert collections.status_code == 200
    assert documents.status_code == 200


async def test_retrieve_rejects_oversized_query(client: AsyncClient, users: dict, qdrant) -> None:
    token = await login(client, OPERATOR_EMAIL)
    response = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "a" * (MAX_QUERY_CHARS + 1)},
        headers=bearer(token),
    )
    assert response.status_code == 422


async def test_internal_long_query_is_clipped(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    """Tasks reuse their prompt as the query; retrieval clips it instead of
    embedding and persisting an unbounded string."""
    await _seed(client)
    await retrieve(db_session, "wolf " * 50_000)
    longest = (
        await db_session.execute(select(func.max(func.length(RetrievalRequest.query))))
    ).scalar_one()
    assert longest == MAX_QUERY_CHARS


async def test_query_embedding_runs_off_event_loop(
    client: AsyncClient, users: dict, qdrant, monkeypatch
) -> None:
    token, _ = await _seed(client)
    real = knowledge_router.get_embedder
    threads: list[threading.Thread] = []

    class _Recording:
        def __init__(self, inner):
            self._inner = inner

        def embed(self, texts):
            threads.append(threading.current_thread())
            return self._inner.embed(texts)

    monkeypatch.setattr(knowledge_router, "get_embedder", lambda name=None: _Recording(real(name)))

    response = await client.post(
        "/api/v1/knowledge/retrieve", json={"query": "wolf spiders"}, headers=bearer(token)
    )

    assert response.status_code == 200
    assert threads and all(t is not threading.main_thread() for t in threads)
