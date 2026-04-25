"""
app/schemas/__init__.py
=======================
Re-exports all public schemas for clean imports:
    from app.schemas import ChatRequest, ChatResponse, DocumentUpload, ...
"""

from app.schemas.auth import Token, TokenData, UserCreate, UserLogin
from app.schemas.chat import ChatRequest, ChatResponse, SourceReference, StreamChunk
from app.schemas.document import (
    CollectionCreate,
    CollectionRead,
    DocumentRead,
    DocumentUpload,
    IndexingStatus,
)

__all__ = [
    # Auth
    "Token",
    "TokenData",
    "UserCreate",
    "UserLogin",
    # Chat
    "ChatRequest",
    "ChatResponse",
    "SourceReference",
    "StreamChunk",
    # Documents
    "CollectionCreate",
    "CollectionRead",
    "DocumentRead",
    "DocumentUpload",
    "IndexingStatus",
]
