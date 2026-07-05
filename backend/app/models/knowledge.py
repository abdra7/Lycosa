import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class DocumentStatus(enum.StrEnum):
    UPLOADED = "uploaded"
    EMBEDDED = "embedded"
    FAILED = "failed"


class JobStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _enum(enum_cls: type[enum.StrEnum]) -> Enum:
    return Enum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        length=20,
    )


class KnowledgeCollection(UUIDPkMixin, TimestampMixin, Base):
    """A named body of knowledge. `node_id` is null while the controller hosts
    all vectors; multi-knowledge-node federation fills it in later sprints."""

    __tablename__ = "knowledge_collections"

    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text())
    embedding_backend: Mapped[str] = mapped_column(String(50))
    embedding_dim: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nodes.id"))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    documents: Mapped[list["Document"]] = relationship(back_populates="collection")


class Document(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_collections.id"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus), default=DocumentStatus.UPLOADED
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text())

    collection: Mapped[KnowledgeCollection] = relationship(back_populates="documents")


class EmbeddingJob(UUIDPkMixin, Base):
    """Trace of one embedding run for a document."""

    __tablename__ = "embedding_jobs"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.RUNNING)
    chunks_embedded: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text())
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetrievalRequest(UUIDPkMixin, Base):
    """Audit trail of retrievals routed through the Knowledge Router."""

    __tablename__ = "retrieval_requests"

    query: Mapped[str] = mapped_column(Text())
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_collections.id")
    )  # null = federated across all collections
    top_k: Mapped[int] = mapped_column(Integer)
    results_count: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[float] = mapped_column(Float)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    requested_by_api_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_keys.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
