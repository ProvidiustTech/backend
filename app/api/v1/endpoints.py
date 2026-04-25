"""
app/api/v1/endpoints.py
========================
Collection and Document management endpoints:
  POST   /api/v1/collections          — create collection
  GET    /api/v1/collections          — list collections
  DELETE /api/v1/collections/{id}     — delete collection + all documents

  POST   /api/v1/documents/upload     — upload + index a text document
  GET    /api/v1/documents/{id}       — get document status
  DELETE /api/v1/documents/{id}       — delete document + its chunks

  GET    /api/v1/health               — health check (no auth required)
"""

import time
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_current_user
from app.core.logging import get_logger
from app.models.collection import Collection
from app.models.document import Document
from app.models.user import User
from app.schemas.document import (
    CollectionCreate,
    CollectionRead,
    DocumentRead,
    IndexingStatus,
)
from app.services.database import check_db_health, get_db
from app.services.vector_store import index_document

log = get_logger(__name__)

router = APIRouter(tags=["Collections & Documents"])


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health", include_in_schema=True, tags=["Health"])
async def health_check():
    """
    Lightweight health check for load balancer probes.
    Checks DB connectivity.
    """
    db_health = await check_db_health()
    return {
        "status": "healthy",
        "database": db_health,
        "service": "IntegrateAI Blueprint",
    }


# ── Collections ───────────────────────────────────────────────────────────────

@router.post(
    "/collections",
    response_model=CollectionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a document collection",
)
async def create_collection(
    payload: CollectionCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> CollectionRead:
    """
    Create a named collection to group related documents.
    Each collection maps to an isolated pgvector table.
    """
    collection = Collection(
        name=payload.name,
        description=payload.description,
        vertical=payload.vertical,
        metadata_=payload.metadata,
    )
    db.add(collection)
    await db.flush()
    await db.refresh(collection)

    log.info("collection created", id=str(collection.id), name=collection.name)

    return CollectionRead(
        id=collection.id,
        name=collection.name,
        description=collection.description,
        vertical=collection.vertical,
        metadata=collection.metadata_,
        document_count=0,
        chunk_count=0,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
    )


@router.get(
    "/collections",
    response_model=list[CollectionRead],
    summary="List all collections",
)
async def list_collections(
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> list[CollectionRead]:
    result = await db.execute(select(Collection).order_by(Collection.created_at.desc()))
    collections = result.scalars().all()
    return [
        CollectionRead(
            id=c.id,
            name=c.name,
            description=c.description,
            vertical=c.vertical,
            metadata=c.metadata_,
            document_count=c.document_count,
            chunk_count=c.chunk_count,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in collections
    ]


@router.delete("/collections/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """Delete a collection and all its documents (cascade)."""
    result = await db.execute(select(Collection).where(Collection.id == collection_id))
    collection = result.scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    await db.delete(collection)
    log.info("collection deleted", id=str(collection_id))


# ── Documents ─────────────────────────────────────────────────────────────────

async def _index_document_background(
    document_id: str,
    collection_id: str,
    title: str,
    text: str,
    metadata: dict,
    db_url: str,
) -> None:
    """
    Background task: index document and update status in DB.
    Runs outside the request context, uses its own DB session.
    """
    from app.services.database import get_db_context

    try:
        chunk_count = await index_document(
            collection_id=collection_id,
            document_id=document_id,
            title=title,
            text=text,
            metadata=metadata,
        )

        async with get_db_context() as db:
            result = await db.execute(select(Document).where(Document.id == document_id))
            doc = result.scalar_one_or_none()
            if doc:
                doc.status = "ready"
                doc.chunk_count = chunk_count

            # Update collection chunk count
            coll_result = await db.execute(
                select(Collection).where(Collection.id == collection_id)
            )
            coll = coll_result.scalar_one_or_none()
            if coll:
                coll.chunk_count = (coll.chunk_count or 0) + chunk_count

        log.info("background indexing complete", doc_id=document_id, chunks=chunk_count)

    except Exception as e:
        log.error("background indexing failed", doc_id=document_id, error=str(e))
        async with get_db_context() as db:
            result = await db.execute(select(Document).where(Document.id == document_id))
            doc = result.scalar_one_or_none()
            if doc:
                doc.status = "failed"


@router.post(
    "/documents/upload",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload and index a document",
)
async def upload_document(
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(..., description="Text or PDF file to index"),
    collection_id: uuid.UUID = Form(...),
    title: str = Form(...),
) -> DocumentRead:
    """
    Upload a document file and trigger async indexing.
    Returns immediately with status='indexing'.
    Poll GET /documents/{id} for status updates.
    """
    # Validate collection exists
    coll_result = await db.execute(
        select(Collection).where(Collection.id == collection_id)
    )
    if not coll_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Collection not found")

    # Read file content
    content_bytes = await file.read()
    if len(content_bytes) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")

    # Decode text (handle PDF extraction in production via a separate parser)
    try:
        text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=415,
            detail="Only UTF-8 text files are supported. Use the /documents/upload-pdf endpoint for PDFs.",
        )

    # Create document record
    doc = Document(
        collection_id=collection_id,
        title=title,
        status="indexing",
        doc_metadata={"filename": file.filename, "content_type": file.content_type},
    )
    db.add(doc)
    await db.flush()

    # Update collection document count
    coll_result2 = await db.execute(select(Collection).where(Collection.id == collection_id))
    coll = coll_result2.scalar_one_or_none()
    if coll:
        coll.document_count = (coll.document_count or 0) + 1

    # Kick off background indexing (does not block response)
    background_tasks.add_task(
        _index_document_background,
        document_id=str(doc.id),
        collection_id=str(collection_id),
        title=title,
        text=text,
        metadata={"filename": file.filename},
        db_url=settings.DATABASE_URL,
    )

    log.info(
        "document upload accepted",
        doc_id=str(doc.id),
        collection_id=str(collection_id),
        title=title,
    )

    return DocumentRead(
        id=doc.id,
        collection_id=collection_id,
        title=doc.title,
        source_url=doc.source_url,
        metadata=doc.doc_metadata,
        chunk_count=0,
        status="indexing",
        created_at=doc.created_at,
    )


@router.get("/documents/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> DocumentRead:
    """Get document details and indexing status."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return DocumentRead(
        id=doc.id,
        collection_id=doc.collection_id,
        title=doc.title,
        source_url=doc.source_url,
        metadata=doc.doc_metadata,
        chunk_count=doc.chunk_count,
        status=doc.status,
        created_at=doc.created_at,
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """Delete a document and all its vector chunks."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    await db.delete(doc)
    log.info("document deleted", doc_id=str(document_id))


# Needed for background task
from app.core.config import settings  # noqa: E402
