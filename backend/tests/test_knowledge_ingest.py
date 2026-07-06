import sys
import uuid
from io import BytesIO

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmbeddingJob
from app.services.knowledge.embedder import EmbedderUnavailableError, get_embedder
from tests.conftest import ADMIN_EMAIL, OPERATOR_EMAIL, bearer, login

MARKDOWN = b"""# Wolf spiders

Lycosa is a genus of wolf spiders. Wolf spiders hunt their prey
instead of spinning webs to catch it.

## Venom

Lycosa venom is generally harmless to humans, causing only minor
swelling and itching in most cases.
"""


def _build_pdf(text: str) -> bytes:
    """Minimal valid single-page PDF with one text object and a correct xref."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return out


PDF = _build_pdf("Wolf spiders hunt at night on the ground")


async def create_collection(client: AsyncClient, token: str, name: str = "spiders") -> dict:
    response = await client.post(
        "/api/v1/knowledge/collections",
        json={"name": name, "description": "arachnid facts"},
        headers=bearer(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def upload(
    client: AsyncClient, token: str, collection_id: str, filename: str, data: bytes
) -> dict:
    response = await client.post(
        f"/api/v1/knowledge/collections/{collection_id}/documents",
        files={"file": (filename, data, "application/octet-stream")},
        headers=bearer(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_collection(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    collection = await create_collection(client, token)
    assert collection["name"] == "spiders"
    assert collection["embedding_backend"] == "hashing"
    assert collection["embedding_dim"] == 384


async def test_duplicate_collection_name_is_409(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    await create_collection(client, token)
    response = await client.post(
        "/api/v1/knowledge/collections", json={"name": "spiders"}, headers=bearer(token)
    )
    assert response.status_code == 409


async def test_markdown_ingest_embeds_chunks(
    client: AsyncClient, users: dict, qdrant, db_session: AsyncSession
) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token)
    document = await upload(client, token, collection["id"], "spiders.md", MARKDOWN)

    assert document["status"] == "embedded"
    assert document["chunk_count"] >= 1

    job = (
        await db_session.execute(
            select(EmbeddingJob).where(EmbeddingJob.document_id == uuid.UUID(document["id"]))
        )
    ).scalar_one()
    assert job.status == "succeeded"
    assert job.chunks_embedded == document["chunk_count"]
    assert job.finished_at is not None


async def test_pdf_ingest_extracts_text(client: AsyncClient, users: dict, qdrant) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="pdf-docs")
    document = await upload(client, token, collection["id"], "spiders.pdf", PDF)

    assert document["status"] == "embedded", document["error"]
    assert document["chunk_count"] >= 1


async def test_empty_document_fails_without_500(client: AsyncClient, users: dict, qdrant) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="empty-docs")
    document = await upload(client, token, collection["id"], "empty.txt", b"   ")

    assert document["status"] == "failed"
    assert "no extractable text" in document["error"]


async def test_documents_listed(client: AsyncClient, users: dict, qdrant) -> None:
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token)
    await upload(client, token, collection["id"], "spiders.md", MARKDOWN)

    listed = await client.get(
        f"/api/v1/knowledge/collections/{collection['id']}/documents", headers=bearer(token)
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["filename"] == "spiders.md"


def _build_encrypted_pdf() -> bytes:
    """A password-protected PDF (RC4 so no extra crypto deps are needed)."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(user_password="secret", algorithm="RC4-128")
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


async def test_corrupt_pdf_fails_with_clear_error(client: AsyncClient, users: dict, qdrant) -> None:
    """Ticket #101: pypdf parse failures must surface as an actionable message,
    not a raw parser traceback string."""
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="corrupt-docs")
    document = await upload(
        client, token, collection["id"], "broken.pdf", b"%PDF-1.4 not a real pdf"
    )

    assert document["status"] == "failed"
    assert "could not parse PDF" in document["error"]


async def test_encrypted_pdf_fails_with_clear_error(
    client: AsyncClient, users: dict, qdrant
) -> None:
    """Ticket #101: encrypted PDFs must be reported as password-protected."""
    token = await login(client, OPERATOR_EMAIL)
    collection = await create_collection(client, token, name="encrypted-docs")
    document = await upload(client, token, collection["id"], "secret.pdf", _build_encrypted_pdf())

    assert document["status"] == "failed"
    assert "password-protected" in document["error"]


async def test_qdrant_unreachable_fails_with_clear_error(client: AsyncClient, users: dict) -> None:
    """Ticket #101: a down/unreachable Qdrant must name Qdrant and its URL so the
    operator knows which service to check."""
    from qdrant_client import AsyncQdrantClient

    from app.services.knowledge import store

    unreachable = AsyncQdrantClient(url="http://127.0.0.1:1", timeout=1)
    store.set_qdrant(unreachable)
    try:
        token = await login(client, OPERATOR_EMAIL)
        collection = await create_collection(client, token, name="qdrant-down")
        document = await upload(client, token, collection["id"], "spiders.md", MARKDOWN)
    finally:
        store.set_qdrant(None)
        await unreachable.close()

    assert document["status"] == "failed"
    assert "Qdrant" in document["error"]


async def test_fastembed_not_installed_gives_actionable_error(monkeypatch) -> None:
    """Ticket #101: a missing fastembed extra must say how to install it instead
    of a bare ImportError."""
    monkeypatch.setitem(sys.modules, "fastembed", None)  # forces ImportError
    get_embedder.cache_clear()
    try:
        with pytest.raises(EmbedderUnavailableError, match="embeddings"):
            get_embedder("fastembed")
    finally:
        get_embedder.cache_clear()


async def test_upload_requires_operator(client: AsyncClient, users: dict, qdrant) -> None:
    token = await login(client, ADMIN_EMAIL)
    collection = await create_collection(client, token)
    response = await client.post(
        f"/api/v1/knowledge/collections/{collection['id']}/documents",
        files={"file": ("x.md", b"data", "text/markdown")},
    )
    assert response.status_code == 401


async def test_collection_create_requires_operator(client: AsyncClient, users: dict) -> None:
    response = await client.post("/api/v1/knowledge/collections", json={"name": "nope"})
    assert response.status_code == 401
