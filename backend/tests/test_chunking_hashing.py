"""UT-BE-01: unit tests for ingestion chunking and hashing.

Covers the pure functions in `app.services.knowledge.loader` (paragraph-aware
chunking, text extraction) and the document-hash/trace metadata written by
`app.services.knowledge.ingestion.ingest_document`.
"""

import hashlib

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DocumentStatus, EmbeddingJob, JobStatus, KnowledgeCollection
from app.services.knowledge.embedder import get_embedder
from app.services.knowledge.ingestion import ingest_document
from app.services.knowledge.loader import ExtractionError, chunk_text, extract_text
from tests.test_knowledge_ingest import MARKDOWN, _build_pdf

# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\n\n\n", " \n\n \n\n "])
def test_empty_or_whitespace_text_yields_no_chunks(text: str) -> None:
    assert chunk_text(text) == []


def test_single_short_paragraph_is_one_stripped_chunk() -> None:
    assert chunk_text("  Wolf spiders hunt at night.  \n\n") == ["Wolf spiders hunt at night."]


def test_small_paragraphs_pack_into_one_chunk() -> None:
    text = f"{'a' * 30}\n\n{'b' * 30}"
    assert chunk_text(text, size=100) == [f"{'a' * 30}\n\n{'b' * 30}"]


def test_packing_respects_size_boundary() -> None:
    # 30 + 30 + 2 (joiner) exceeds size=50, so the second paragraph starts a new chunk
    text = f"{'a' * 30}\n\n{'b' * 30}"
    assert chunk_text(text, size=50) == ["a" * 30, "b" * 30]


def test_paragraph_exactly_size_is_not_split() -> None:
    assert chunk_text("x" * 50, size=50) == ["x" * 50]


def test_oversized_paragraph_hard_split_windows_and_overlap() -> None:
    # 2000 chars, no whitespace, every position identifiable: "000000010002..."
    text = "".join(f"{i:04d}" for i in range(500))
    size, overlap = 100, 20
    chunks = chunk_text(text, size=size, overlap=overlap)

    assert all(len(c) <= size for c in chunks)
    step = size - overlap
    for k, chunk in enumerate(chunks):
        assert chunk == text[k * step : k * step + size]
    # consecutive chunks share exactly `overlap` chars of continuity
    for left, right in zip(chunks, chunks[1:], strict=False):
        assert left[-overlap:] == right[:overlap]
    # nothing lost: de-overlapped concatenation reconstructs the paragraph
    assert chunks[0] + "".join(c[overlap:] for c in chunks[1:]) == text


def test_buffer_flushes_before_oversized_paragraph_and_order_is_kept() -> None:
    big = "z" * 120
    chunks = chunk_text(f"first\n\n{big}\n\nlast", size=50, overlap=10)
    assert chunks[0] == "first"
    assert chunks[-1] == "last"
    assert "".join(chunks[1:-1]).startswith("z")
    assert all(len(c) <= 50 for c in chunks)


def test_default_size_and_overlap_bound_chunk_length() -> None:
    text = "\n\n".join("word " * 400 for _ in range(3))  # three ~2000-char paragraphs
    chunks = chunk_text(text)
    assert len(chunks) > 3
    assert all(len(c) <= 800 for c in chunks)


# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------


def test_plain_text_decodes_utf8() -> None:
    assert extract_text("notes.md", "wolf spiders — Lycosa".encode()) == "wolf spiders — Lycosa"


def test_invalid_utf8_is_replaced_not_raised() -> None:
    text = extract_text("notes.txt", b"wolf \xff\xfe spiders")
    assert "wolf" in text and "spiders" in text
    assert "�" in text  # replacement character, no exception


def test_pdf_extension_is_case_insensitive() -> None:
    text = extract_text("SPIDERS.PDF", _build_pdf("Wolf spiders hunt at night"))
    assert "Wolf spiders hunt at night" in text


def test_corrupt_pdf_raises_extraction_error_naming_the_file() -> None:
    with pytest.raises(ExtractionError, match="bad.pdf"):
        extract_text("bad.pdf", b"%PDF-1.4 not really a pdf")


def test_extraction_error_is_a_value_error() -> None:
    # the API layer maps ValueError to a 4xx; ExtractionError must stay a subclass
    assert issubclass(ExtractionError, ValueError)


# ---------------------------------------------------------------------------
# ingest_document: hashing + trace metadata
# ---------------------------------------------------------------------------


async def make_collection(db_session: AsyncSession, name: str = "ut-be-01") -> KnowledgeCollection:
    embedder = get_embedder("hashing")
    collection = KnowledgeCollection(
        name=name, embedding_backend=embedder.name, embedding_dim=embedder.dim
    )
    db_session.add(collection)
    await db_session.commit()
    await db_session.refresh(collection)
    return collection


async def test_document_sha256_and_size_recorded(db_session: AsyncSession, qdrant) -> None:
    collection = await make_collection(db_session)
    document = await ingest_document(db_session, collection, "wolves.md", "text/markdown", MARKDOWN)

    assert document.sha256 == hashlib.sha256(MARKDOWN).hexdigest()
    assert document.size_bytes == len(MARKDOWN)
    assert document.status == DocumentStatus.EMBEDDED
    assert document.chunk_count > 0

    job = (
        await db_session.execute(
            select(EmbeddingJob).where(EmbeddingJob.document_id == document.id)
        )
    ).scalar_one()
    assert job.status == JobStatus.SUCCEEDED
    assert job.chunks_embedded == document.chunk_count
    assert job.finished_at is not None


async def test_identical_content_gets_same_hash_as_distinct_documents(
    db_session: AsyncSession, qdrant
) -> None:
    collection = await make_collection(db_session)
    first = await ingest_document(db_session, collection, "a.md", "text/markdown", MARKDOWN)
    second = await ingest_document(db_session, collection, "b.md", "text/markdown", MARKDOWN)

    assert first.id != second.id  # no dedupe: the hash is integrity metadata
    assert first.sha256 == second.sha256


async def test_failed_extraction_records_failure_with_hash_and_does_not_raise(
    db_session: AsyncSession,
) -> None:
    collection = await make_collection(db_session)
    data = b"%PDF-1.4 not really a pdf"
    document = await ingest_document(db_session, collection, "bad.pdf", "application/pdf", data)

    assert document.status == DocumentStatus.FAILED
    assert document.error and "bad.pdf" in document.error
    assert document.sha256 == hashlib.sha256(data).hexdigest()  # hashed before the pipeline ran

    job = (
        await db_session.execute(
            select(EmbeddingJob).where(EmbeddingJob.document_id == document.id)
        )
    ).scalar_one()
    assert job.status == JobStatus.FAILED
    assert job.finished_at is not None
