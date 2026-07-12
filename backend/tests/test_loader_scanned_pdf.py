"""Scanned / image-only PDF handling (issue #29).

pypdf extracts a text layer only; a scanned PDF has none, so extraction used to
bubble up a generic "no extractable text in document". Now a no-text-layer PDF
either goes through the optional OCR path (pytesseract + Pillow extra, plus the
tesseract binary) or fails with an operator-facing message that says the PDF
looks like a scan and how to enable OCR. CI has no OCR stack installed, so the
OCR path itself is exercised via monkeypatching `_ocr_pdf`.
"""

from io import BytesIO

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DocumentStatus, KnowledgeCollection
from app.services.knowledge import loader
from app.services.knowledge.embedder import get_embedder
from app.services.knowledge.ingestion import ingest_document
from app.services.knowledge.loader import ExtractionError, extract_text


def _build_textless_pdf(pages: int = 1) -> bytes:
    """A structurally valid PDF whose pages carry no text layer — extraction
    sees exactly what a scanned page yields: empty strings."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_textless_pdf_without_ocr_reports_scan_and_how_to_enable_ocr() -> None:
    with pytest.raises(ExtractionError, match=r"scan\.pdf.*no text layer") as excinfo:
        extract_text("scan.pdf", _build_textless_pdf())
    # the message must be actionable for an operator, not a generic failure
    assert "OCR" in str(excinfo.value)


def test_textless_pdf_uses_ocr_text_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "_ocr_pdf", lambda filename, reader: "Scanned safety manual.")
    text = extract_text("scan.pdf", _build_textless_pdf())
    assert text == "Scanned safety manual."


def test_ocr_yielding_no_text_still_fails_with_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OCR stack installed but the pages contain no readable text
    monkeypatch.setattr(loader, "_ocr_pdf", lambda filename, reader: "")
    with pytest.raises(ExtractionError, match="scan.pdf"):
        extract_text("scan.pdf", _build_textless_pdf())


def test_ocr_helper_returns_none_without_the_ocr_stack() -> None:
    # dev/CI installs no pytesseract/Pillow — the helper must degrade to None,
    # never crash, so the operator message path is taken
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(_build_textless_pdf()))
    assert loader._ocr_pdf("scan.pdf", reader) is None


async def test_scanned_pdf_lands_failed_with_actionable_error(
    db_session: AsyncSession, qdrant
) -> None:
    embedder = get_embedder("hashing")
    collection = KnowledgeCollection(
        name="scan-e2e", embedding_backend=embedder.name, embedding_dim=embedder.dim
    )
    db_session.add(collection)
    await db_session.commit()
    await db_session.refresh(collection)

    document = await ingest_document(
        db_session, collection, "contract-scan.pdf", "application/pdf", _build_textless_pdf(3)
    )

    assert document.status == DocumentStatus.FAILED
    assert document.error is not None
    assert "no text layer" in document.error
    assert "OCR" in document.error
