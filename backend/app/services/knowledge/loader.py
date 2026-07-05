"""Document loading and chunking: bytes in, text chunks out."""

from io import BytesIO


def extract_text(filename: str, data: bytes) -> str:
    """PDF via pypdf; everything else treated as UTF-8 text (md/txt/code)."""
    if filename.lower().endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    return data.decode("utf-8", errors="replace")


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """Paragraph-aware chunking: pack paragraphs up to `size` chars; oversized
    paragraphs are hard-split with `overlap` chars of continuity."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        if len(paragraph) > size:
            flush()
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + size].strip())
                start += size - overlap
        elif len(current) + len(paragraph) + 2 > size:
            flush()
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    flush()
    return chunks
