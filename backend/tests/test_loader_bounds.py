"""Security audit 2026-10 (ADR-030): extraction output is bounded for every
format, and parsing runs off the controller's event loop."""

import threading

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.knowledge import ingestion, loader
from app.services.knowledge.loader import ExtractionError, extract_text
from tests.test_chunking_hashing import make_collection
from tests.test_knowledge_ingest import _build_pdf
from tests.test_loader_docx import _build_docx

CAP = 50_000


@pytest.fixture
def small_cap(monkeypatch):
    monkeypatch.setattr(loader, "MAX_EXTRACTED_CHARS", CAP)


def test_default_cap_matches_the_raw_upload_limit() -> None:
    from app.api.v1.knowledge import MAX_UPLOAD_BYTES

    assert loader.MAX_EXTRACTED_CHARS == MAX_UPLOAD_BYTES


def test_csv_label_repetition_is_capped(small_cap) -> None:
    # a 1,000-char header repeated on 100 rows expands ~1000x past the raw size
    data = b"h" * 1_000 + b"\n" + b"x\n" * 100
    with pytest.raises(ExtractionError, match="extraction limit"):
        extract_text("amp.csv", data)


def test_json_key_path_repetition_is_capped(small_cap) -> None:
    data = b'{"' + b"k" * 1_000 + b'": [' + b",".join([b"0"] * 100) + b"]}"
    with pytest.raises(ExtractionError, match="extraction limit"):
        extract_text("amp.json", data)


def test_pdf_text_is_capped(small_cap, monkeypatch) -> None:
    monkeypatch.setattr(loader, "MAX_EXTRACTED_CHARS", 10)
    with pytest.raises(ExtractionError, match="extraction limit"):
        extract_text("long.pdf", _build_pdf("wolf spiders hunt at night"))


def test_docx_declared_uncompressed_size_is_checked_before_parsing(monkeypatch) -> None:
    import docx

    data = _build_docx(["hello"])
    monkeypatch.setattr(loader, "MAX_DOCX_UNCOMPRESSED_BYTES", 100)
    opened = []
    monkeypatch.setattr(docx, "Document", lambda *a, **k: opened.append(1))
    with pytest.raises(ExtractionError, match="uncompressed"):
        extract_text("bomb.docx", data)
    assert opened == []  # python-docx never saw the archive


def test_within_cap_documents_still_extract(small_cap) -> None:
    assert "name: Ada" in extract_text("people.csv", b"name,role\nAda,eng\n")
    assert "a.b: 1" in extract_text("doc.json", b'{"a": {"b": 1}}')
    assert "hello" in extract_text("doc.docx", _build_docx(["hello"]))


async def test_extraction_and_chunking_run_off_the_event_loop(
    db_session: AsyncSession, qdrant, monkeypatch
) -> None:
    seen: list[threading.Thread] = []
    real_extract, real_chunk = ingestion.extract_text, ingestion.chunk_text

    def extract(*args):
        seen.append(threading.current_thread())
        return real_extract(*args)

    def chunk(*args, **kwargs):
        seen.append(threading.current_thread())
        return real_chunk(*args, **kwargs)

    monkeypatch.setattr(ingestion, "extract_text", extract)
    monkeypatch.setattr(ingestion, "chunk_text", chunk)
    collection = await make_collection(db_session)

    document = await ingestion.ingest_document(
        db_session, collection, "notes.md", "text/markdown", b"# Wolf spiders\n\nThey hunt."
    )

    assert document.status.value == "embedded"
    assert len(seen) == 2 and all(t is not threading.main_thread() for t in seen)
