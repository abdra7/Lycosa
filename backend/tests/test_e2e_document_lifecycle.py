"""E2E-01: the full document lifecycle as one operator journey, over HTTP only
(DB/Qdrant touched solely to verify what the API claims happened):

    create collection -> upload -> hash recorded -> chunked -> vectorized
    -> retrieve via query (scoped + federated) -> audit trail written
"""

import hashlib
import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, EmbeddingJob, JobStatus, RetrievalRequest
from app.services.knowledge.store import qdrant_name
from tests.conftest import OPERATOR_EMAIL, bearer, login
from tests.test_knowledge_ingest import _build_pdf, create_collection, upload

# Three keyword-distinct paragraphs, each bulky enough (~500 chars) that the
# chunker cannot pack them together — the lifecycle must produce one chunk per
# topic for the ranking assertions to mean anything.
VENOM = "Lycosa venom causes mild swelling and itching in humans after a bite. " * 8
HUNTING = "Wolf spiders hunt prey at night on the ground and never spin webs. " * 8
HABITAT = "Wolf spiders dig burrows in grasslands and forests for shelter. " * 8
SPIDER_DOC = f"{VENOM.strip()}\n\n{HUNTING.strip()}\n\n{HABITAT.strip()}".encode()


async def test_document_lifecycle_upload_hash_vectorize_retrieve(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    token = await login(client, OPERATOR_EMAIL)

    # -- create + upload -----------------------------------------------------
    collection = await create_collection(client, token, name="wolf-spiders")
    document = await upload(client, token, collection["id"], "spiders.md", SPIDER_DOC)
    assert document["status"] == "embedded", document["error"]
    assert document["size_bytes"] == len(SPIDER_DOC)
    assert document["chunk_count"] >= 3  # one per topic paragraph

    # -- hash + job trace persisted ------------------------------------------
    row = (
        await db_session.execute(select(Document).where(Document.id == uuid.UUID(document["id"])))
    ).scalar_one()
    assert row.sha256 == hashlib.sha256(SPIDER_DOC).hexdigest()
    job = (
        await db_session.execute(select(EmbeddingJob).where(EmbeddingJob.document_id == row.id))
    ).scalar_one()
    assert job.status == JobStatus.SUCCEEDED
    assert job.chunks_embedded == document["chunk_count"]

    # -- vectorized: every chunk landed in Qdrant ----------------------------
    points = await qdrant.count(qdrant_name(uuid.UUID(collection["id"])))
    assert points.count == document["chunk_count"]

    # -- retrieve via query: the right chunk ranks first ---------------------
    response = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "venom swelling itching humans", "collection": "wolf-spiders"},
        headers=bearer(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["chunks"], "expected retrieval results"
    top = body["chunks"][0]
    assert "venom" in top["text"].lower()
    assert top["source"] == "spiders.md"
    assert top["collection"] == "wolf-spiders"
    assert top["document_id"] == document["id"]
    assert body["context_text"]
    # a different topic ranks a different chunk first — retrieval is semantic,
    # not just "return the document"
    response = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "burrows grasslands forests shelter", "collection": "wolf-spiders"},
        headers=bearer(token),
    )
    assert "burrows" in response.json()["chunks"][0]["text"].lower()

    # -- federated: the router finds it without the caller naming anything ---
    response = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "venom swelling itching humans"},
        headers=bearer(token),
    )
    assert response.status_code == 200
    assert response.json()["chunks"][0]["collection"] == "wolf-spiders"

    # -- audit trail: every retrieval was recorded ---------------------------
    audit_rows = (
        (await db_session.execute(select(RetrievalRequest).order_by(RetrievalRequest.created_at)))
        .scalars()
        .all()
    )
    assert len(audit_rows) == 3
    assert all(r.results_count > 0 and r.latency_ms >= 0 for r in audit_rows)
    scoped, _, federated = audit_rows
    assert scoped.collection_id == uuid.UUID(collection["id"])
    assert federated.collection_id is None  # federated = no collection named


async def test_pdf_lifecycle_reaches_retrieval(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    """The same journey through the PDF extraction path: a PDF's text must be
    hashed, embedded, and retrievable just like markdown."""
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="manuals")
    pdf = _build_pdf("The controller schedules tasks across registered nodes")
    document = await upload(client, token, collection["id"], "manual.pdf", pdf)
    assert document["status"] == "embedded", document["error"]

    row = (
        await db_session.execute(select(Document).where(Document.id == uuid.UUID(document["id"])))
    ).scalar_one()
    assert row.sha256 == hashlib.sha256(pdf).hexdigest()

    response = await client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": "controller schedules tasks nodes"},
        headers=bearer(token),
    )
    assert response.status_code == 200
    top = response.json()["chunks"][0]
    assert top["source"] == "manual.pdf"
    assert "schedules tasks" in top["text"]
