"""Ticket #105: DELETE /knowledge/collections/{id} — Qdrant vectors and DB
metadata (documents + embedding jobs) go with the collection."""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, EmbeddingJob
from app.services.knowledge.store import qdrant_name
from tests.conftest import OPERATOR_EMAIL, bearer, login
from tests.test_knowledge_ingest import MARKDOWN, create_collection, upload


async def test_delete_collection_cascades(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="doomed")
    collection_id = uuid.UUID(collection["id"])
    document = await upload(client, token, collection["id"], "spiders.md", MARKDOWN)
    assert document["status"] == "embedded"
    assert await qdrant.collection_exists(qdrant_name(collection_id))

    response = await client.delete(
        f"/api/v1/knowledge/collections/{collection['id']}", headers=bearer(token)
    )
    assert response.status_code == 204, response.text

    listed = await client.get("/api/v1/knowledge/collections", headers=bearer(token))
    assert all(c["id"] != collection["id"] for c in listed.json())

    documents = (
        (await db_session.execute(select(Document).where(Document.collection_id == collection_id)))
        .scalars()
        .all()
    )
    assert documents == []
    jobs = (
        (
            await db_session.execute(
                select(EmbeddingJob).where(EmbeddingJob.document_id == uuid.UUID(document["id"]))
            )
        )
        .scalars()
        .all()
    )
    assert jobs == []
    assert not await qdrant.collection_exists(qdrant_name(collection_id))


async def test_delete_collection_without_documents(
    client: AsyncClient, users: dict, qdrant
) -> None:
    """A collection that never ingested anything has no Qdrant collection;
    deletion must still succeed."""
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="empty-doomed")

    response = await client.delete(
        f"/api/v1/knowledge/collections/{collection['id']}", headers=bearer(token)
    )
    assert response.status_code == 204, response.text


async def test_delete_missing_collection_is_404(client: AsyncClient, users: dict, qdrant) -> None:
    token = await login(client, OPERATOR_EMAIL)
    response = await client.delete(
        f"/api/v1/knowledge/collections/{uuid.uuid4()}", headers=bearer(token)
    )
    assert response.status_code == 404


async def test_delete_collection_requires_operator(
    client: AsyncClient, users: dict, qdrant
) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="guarded")
    response = await client.delete(f"/api/v1/knowledge/collections/{collection['id']}")
    assert response.status_code == 401
