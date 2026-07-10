"""Ticket #105 + IT-API-01: DELETE /knowledge/collections/{id} — Qdrant vectors
and DB metadata (documents + embedding jobs) go with the collection; a Qdrant
outage aborts before any metadata is lost."""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import API_KEY_HEADER
from app.models import AuditLog, Document, EmbeddingJob, RetrievalRequest
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


async def test_delete_forbidden_for_node_api_key(
    client: AsyncClient, users: dict, qdrant, node_api_key: tuple
) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="node-guarded")
    full_key, _ = node_api_key
    response = await client.delete(
        f"/api/v1/knowledge/collections/{collection['id']}", headers={API_KEY_HEADER: full_key}
    )
    assert response.status_code == 403


async def test_delete_aborts_on_qdrant_outage_and_keeps_metadata(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    """The endpoint's ordering guarantee: vectors are dropped first, so a Qdrant
    outage must abort with a 502 while every DB row survives for a retry."""
    from qdrant_client import AsyncQdrantClient

    from app.services.knowledge import store

    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="survivor")
    collection_id = uuid.UUID(collection["id"])
    document = await upload(client, token, collection["id"], "spiders.md", MARKDOWN)
    assert document["status"] == "embedded"

    unreachable = AsyncQdrantClient(url="http://127.0.0.1:1", timeout=1)
    store.set_qdrant(unreachable)
    try:
        response = await client.delete(
            f"/api/v1/knowledge/collections/{collection['id']}", headers=bearer(token)
        )
    finally:
        store.set_qdrant(qdrant)  # hand the fixture's in-memory client back
        await unreachable.close()

    assert response.status_code == 502, response.text
    assert "Qdrant" in response.text

    listed = await client.get("/api/v1/knowledge/collections", headers=bearer(token))
    assert any(c["id"] == collection["id"] for c in listed.json())
    documents = (
        (await db_session.execute(select(Document).where(Document.collection_id == collection_id)))
        .scalars()
        .all()
    )
    assert len(documents) == 1
    jobs = (
        (
            await db_session.execute(
                select(EmbeddingJob).where(EmbeddingJob.document_id == uuid.UUID(document["id"]))
            )
        )
        .scalars()
        .all()
    )
    assert len(jobs) == 1
    audit_rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "knowledge.collection.delete")
            )
        )
        .scalars()
        .all()
    )
    assert audit_rows == []  # nothing was deleted, so nothing may claim it was

    # the aborted delete left a retryable state: with Qdrant back, it succeeds
    retry = await client.delete(
        f"/api/v1/knowledge/collections/{collection['id']}", headers=bearer(token)
    )
    assert retry.status_code == 204, retry.text


async def test_delete_writes_audit_log(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="audited")

    response = await client.delete(
        f"/api/v1/knowledge/collections/{collection['id']}", headers=bearer(token)
    )
    assert response.status_code == 204

    entry = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "knowledge.collection.delete",
                AuditLog.resource_id == collection["id"],
            )
        )
    ).scalar_one()
    assert entry.resource_type == "knowledge_collection"
    assert entry.detail == {"name": "audited"}


async def test_delete_preserves_retrieval_audit_with_null_collection(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    """RetrievalRequest rows are an audit trail: deletion nulls their collection
    reference instead of erasing the history."""
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="queried")
    collection_id = uuid.UUID(collection["id"])
    await upload(client, token, collection["id"], "spiders.md", MARKDOWN)

    retrieved = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "wolf spider venom", "collection": "queried"},
        headers=bearer(token),
    )
    assert retrieved.status_code == 200, retrieved.text
    rows_before = (
        (
            await db_session.execute(
                select(RetrievalRequest).where(RetrievalRequest.collection_id == collection_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows_before) == 1
    retrieval_id = rows_before[0].id

    response = await client.delete(
        f"/api/v1/knowledge/collections/{collection['id']}", headers=bearer(token)
    )
    assert response.status_code == 204

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(RetrievalRequest).where(RetrievalRequest.id == retrieval_id)
        )
    ).scalar_one()
    assert row.collection_id is None
    assert row.query == "wolf spider venom"


async def test_deleted_collection_vectors_not_retrievable(
    client: AsyncClient, users: dict, qdrant
) -> None:
    """The behavioral face of the vector flush: after deletion, neither a named
    nor a federated retrieval can surface the collection's chunks."""
    token = await login(client, OPERATOR_EMAIL)
    doomed = await create_collection(client, token, name="doomed-content")
    keeper = await create_collection(client, token, name="keeper-content")
    await upload(client, token, doomed["id"], "spiders.md", MARKDOWN)
    await upload(
        client,
        token,
        keeper["id"],
        "compose.md",
        b"Docker compose starts the qdrant and postgres services.",
    )

    response = await client.delete(
        f"/api/v1/knowledge/collections/{doomed['id']}", headers=bearer(token)
    )
    assert response.status_code == 204

    named = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "wolf spider venom", "collection": "doomed-content"},
        headers=bearer(token),
    )
    assert named.status_code == 404

    federated = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "wolf spider venom"},
        headers=bearer(token),
    )
    assert federated.status_code == 200, federated.text
    chunks = federated.json()["chunks"]
    assert all(chunk["collection"] != "doomed-content" for chunk in chunks)
    assert all(chunk["source"] != "spiders.md" for chunk in chunks)
