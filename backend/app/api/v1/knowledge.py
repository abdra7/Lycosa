import asyncio
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy import select

from app.api.deps import DbDep, Principal, PrincipalDep, require_roles
from app.db.session import get_runtime_sessionmaker
from app.models import Document, KnowledgeCollection
from app.models.user import ROLE_ADMIN, ROLE_OPERATOR
from app.schemas.knowledge import (
    CollectionCreate,
    CollectionOut,
    DocumentOut,
    RetrieveRequest,
    RetrieveResponse,
)
from app.services.audit import audit
from app.services.knowledge.embedder import get_embedder
from app.services.knowledge.ingestion import ingest_document
from app.services.knowledge.router import UnknownCollectionError, retrieve

logger = logging.getLogger("lycosa.knowledge")

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

OperatorDep = Annotated[Principal, Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR))]

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB per document (v1)


async def _get_collection(db: DbDep, collection_id: uuid.UUID) -> KnowledgeCollection:
    collection = (
        await db.execute(select(KnowledgeCollection).where(KnowledgeCollection.id == collection_id))
    ).scalar_one_or_none()
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")
    return collection


@router.post("/collections", response_model=CollectionOut, status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: CollectionCreate, principal: OperatorDep, request: Request, db: DbDep
) -> CollectionOut:
    existing = (
        await db.execute(select(KnowledgeCollection).where(KnowledgeCollection.name == body.name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Collection {body.name!r} already exists",
        )
    embedder = get_embedder()
    collection = KnowledgeCollection(
        name=body.name,
        description=body.description,
        embedding_backend=embedder.name,
        embedding_dim=embedder.dim,
        created_by_user_id=principal.id if principal.type == "user" else None,
    )
    db.add(collection)
    await db.flush()
    await audit(
        db,
        action="knowledge.collection.create",
        actor_user_id=principal.id if principal.type == "user" else None,
        resource_type="knowledge_collection",
        resource_id=str(collection.id),
        detail={"name": body.name},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(collection)
    return CollectionOut.model_validate(collection)


@router.get("/collections", response_model=list[CollectionOut])
async def list_collections(db: DbDep, _principal: PrincipalDep) -> list[CollectionOut]:
    collections = (
        (await db.execute(select(KnowledgeCollection).order_by(KnowledgeCollection.name)))
        .scalars()
        .all()
    )
    return [CollectionOut.model_validate(c) for c in collections]


@router.post(
    "/collections/{collection_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    collection_id: uuid.UUID, file: UploadFile, _principal: OperatorDep, db: DbDep
) -> DocumentOut:
    """Upload and synchronously ingest a document (text/markdown/code/PDF).

    Extraction or embedding problems are reported on the returned document's
    `status`/`error`, not as a 5xx.

    Ingestion runs shielded, on its own DB session: if the caller times out or
    disconnects mid-run, the pipeline still finishes and records its terminal
    state (Ticket #104) — the document lands on embedded/failed instead of
    hanging in 'uploaded' with its job 'running' forever."""
    collection = await _get_collection(db, collection_id)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Document exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )
    filename = file.filename or "unnamed"
    content_type = file.content_type

    async def _run() -> Document:
        async with get_runtime_sessionmaker()() as session:
            return await ingest_document(session, collection, filename, content_type, data)

    dispatch = asyncio.create_task(_run())
    dispatch.add_done_callback(_log_orphaned_ingestion)
    document = await asyncio.shield(dispatch)
    return DocumentOut.model_validate(document)


def _log_orphaned_ingestion(dispatch: "asyncio.Task[Document]") -> None:
    """An ingestion that outlives its request has nobody awaiting it; surface
    unexpected crashes in the log instead of a silent 'never retrieved'."""
    if not dispatch.cancelled() and dispatch.exception() is not None:
        logger.error(
            "document ingestion failed after client disconnect", exc_info=dispatch.exception()
        )


@router.get("/collections/{collection_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    collection_id: uuid.UUID, db: DbDep, _principal: PrincipalDep
) -> list[DocumentOut]:
    await _get_collection(db, collection_id)
    documents = (
        (
            await db.execute(
                select(Document)
                .where(Document.collection_id == collection_id)
                .order_by(Document.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [DocumentOut.model_validate(d) for d in documents]


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve_knowledge(
    body: RetrieveRequest, principal: PrincipalDep, db: DbDep
) -> RetrieveResponse:
    """Semantic retrieval via the Knowledge Router. The caller never names a
    node; omit `collection` to search across all collections (federated)."""
    try:
        result = await retrieve(
            db,
            body.query,
            collection_name=body.collection,
            top_k=body.top_k,
            requested_by_user_id=principal.id if principal.type == "user" else None,
            requested_by_api_key_id=principal.id if principal.type == "api_key" else None,
        )
    except UnknownCollectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Collection {exc.args[0]!r} not found"
        ) from None
    return RetrieveResponse(**result.model_dump())
