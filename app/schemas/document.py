"""app/schemas/document.py — Document and Collection schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    vertical: str | None = Field(
        default=None,
        description="SME vertical: finance | healthcare | manufacturing | retail | logistics",
    )


class CollectionRead(BaseModel):
    id: UUID
    name: str
    description: str | None
    vertical: str | None
    metadata: dict[str, Any]
    document_count: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentUpload(BaseModel):
    collection_id: UUID
    title: str = Field(min_length=1, max_length=512)
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentRead(BaseModel):
    id: UUID
    collection_id: UUID
    title: str
    source_url: str | None
    metadata: dict[str, Any]
    chunk_count: int
    status: str  # pending | indexing | ready | failed
    created_at: datetime

    model_config = {"from_attributes": True}


class IndexingStatus(BaseModel):
    document_id: UUID
    status: str
    chunks_created: int
    error: str | None = None
    elapsed_ms: float
