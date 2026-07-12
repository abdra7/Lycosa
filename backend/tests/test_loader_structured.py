"""Structure-aware CSV/JSON loaders (ADR-024).

CSV rows and JSON records are turned into row/record-oriented text units so
retrieval can isolate a single record, instead of being decoded as one opaque
UTF-8 blob. Malformed input raises ExtractionError (like the PDF path) so
ingestion records a clean failure rather than a 500.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DocumentStatus, KnowledgeCollection
from app.services.knowledge.embedder import get_embedder
from app.services.knowledge.ingestion import ingest_document
from app.services.knowledge.loader import ExtractionError, chunk_text, extract_text

# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def test_csv_header_becomes_field_labels_per_row() -> None:
    csv = b"name,legs,habitat\nwolf spider,8,burrow\ntarantula,8,desert\n"
    text = extract_text("spiders.csv", csv)
    rows = text.split("\n\n")
    assert rows[0] == "name: wolf spider | legs: 8 | habitat: burrow"
    assert rows[1] == "name: tarantula | legs: 8 | habitat: desert"


def test_csv_rows_are_separate_paragraphs_so_chunking_can_isolate_them() -> None:
    csv = b"q,a\nhabitat?,burrow\ndiet?,insects\n"
    chunks = chunk_text(extract_text("faq.csv", csv), size=40)
    # each short row fits its own chunk at this size
    assert "q: habitat? | a: burrow" in chunks
    assert "q: diet? | a: insects" in chunks


def test_csv_quoted_commas_and_ragged_rows() -> None:
    csv = b'name,note\n"wolf, spider","hunts, at night"\nsolo\n'
    text = extract_text("x.csv", csv)
    rows = text.split("\n\n")
    assert rows[0] == "name: wolf, spider | note: hunts, at night"
    # ragged row (fewer columns) keeps the labels it has, no crash
    assert rows[1] == "name: solo"


def test_csv_with_only_a_header_and_no_rows_is_an_extraction_error() -> None:
    with pytest.raises(ExtractionError, match="empty.csv"):
        extract_text("empty.csv", b"name,legs\n")


def test_csv_extension_is_case_insensitive() -> None:
    text = extract_text("DATA.CSV", b"k,v\nfoo,bar\n")
    assert text == "k: foo | v: bar"


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_json_object_flattens_to_path_value_lines() -> None:
    data = b'{"name": "wolf spider", "legs": 8, "habitat": {"type": "burrow"}}'
    text = extract_text("spider.json", data)
    lines = text.split("\n")
    assert "name: wolf spider" in lines
    assert "legs: 8" in lines
    assert "habitat.type: burrow" in lines


def test_json_array_of_objects_makes_one_paragraph_per_record() -> None:
    data = b'[{"name": "a"}, {"name": "b"}]'
    text = extract_text("list.json", data)
    assert text.split("\n\n") == ["name: a", "name: b"]


def test_json_nested_arrays_use_index_paths() -> None:
    data = b'{"tags": ["fast", "nocturnal"]}'
    text = extract_text("t.json", data)
    lines = text.split("\n")
    assert "tags[0]: fast" in lines
    assert "tags[1]: nocturnal" in lines


def test_json_scalars_are_json_typed() -> None:
    data = b'{"a": null, "b": true, "c": false, "d": 3.5}'
    text = extract_text("s.json", data)
    lines = text.split("\n")
    assert "a: null" in lines
    assert "b: true" in lines
    assert "c: false" in lines
    assert "d: 3.5" in lines


def test_malformed_json_raises_extraction_error_naming_the_file() -> None:
    with pytest.raises(ExtractionError, match="bad.json"):
        extract_text("bad.json", b"{not valid json")


# ---------------------------------------------------------------------------
# regressions: plain text/markdown unchanged
# ---------------------------------------------------------------------------


def test_plain_markdown_still_decoded_as_text() -> None:
    assert extract_text("notes.md", b"# Wolf spiders\n\nhunt at night") == (
        "# Wolf spiders\n\nhunt at night"
    )


def test_csv_like_content_in_a_txt_file_is_left_as_text() -> None:
    # routing is by extension, like the PDF path — a .txt is not parsed as CSV
    assert extract_text("data.txt", b"a,b\n1,2") == "a,b\n1,2"


# ---------------------------------------------------------------------------
# end-to-end: a CSV ingests into multiple retrievable chunks
# ---------------------------------------------------------------------------


async def test_csv_ingests_into_multiple_row_chunks(db_session: AsyncSession, qdrant) -> None:
    embedder = get_embedder("hashing")
    collection = KnowledgeCollection(
        name="csv-e2e", embedding_backend=embedder.name, embedding_dim=embedder.dim
    )
    db_session.add(collection)
    await db_session.commit()
    await db_session.refresh(collection)

    # 30 short rows: joined as paragraphs, chunk_text packs them into >1 chunk
    header = b"id,fact\n"
    rows = b"".join(f"{i},spider fact number {i}\n".encode() for i in range(30))
    document = await ingest_document(db_session, collection, "facts.csv", "text/csv", header + rows)

    assert document.status == DocumentStatus.EMBEDDED
    assert document.chunk_count > 1  # rows split across chunks, not one opaque blob
