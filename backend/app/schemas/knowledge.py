import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.knowledge import DocumentStatus
from app.services.knowledge.router import RetrievedChunk


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    description: str | None = None


class CollectionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    description: str | None
    embedding_backend: str
    embedding_dim: int
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    collection_id: uuid.UUID
    filename: str
    content_type: str | None
    size_bytes: int
    status: DocumentStatus
    chunk_count: int
    error: str | None
    created_at: datetime


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1)
    collection: str | None = None  # omit to let the Knowledge Router choose
    top_k: int = Field(default=5, ge=1, le=20)


class RetrieveResponse(BaseModel):
    chunks: list[RetrievedChunk]
    context_text: str
    latency_ms: float
