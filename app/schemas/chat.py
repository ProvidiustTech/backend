"""
app/schemas/chat.py
===================
Request/response models for the /chat endpoint.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Inbound chat request from client."""

    query: str = Field(
        min_length=1,
        max_length=4096,
        description="The user's question or message",
        examples=["What were our Q3 revenue figures?"],
    )
    collection_id: UUID = Field(
        description="UUID of the document collection to query",
    )
    conversation_id: UUID | None = Field(
        default=None,
        description="Optional: continue an existing conversation for multi-turn context",
    )
    metadata_filter: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata filters applied before retrieval. "
                    "Example: {'department': 'finance', 'year': 2024}",
    )
    stream: bool = Field(
        default=True,
        description="If true, returns a Server-Sent Events stream. If false, returns JSON.",
    )


class SourceReference(BaseModel):
    """A document chunk cited in the answer."""

    doc_id: str
    doc_title: str
    page: int | None = None
    relevance_score: float = Field(ge=0.0, le=1.0)
    excerpt: str = Field(max_length=500, description="Short snippet from the chunk")


class ChatResponse(BaseModel):
    """Non-streaming chat response."""

    answer: str
    sources: list[SourceReference]
    conversation_id: str
    collection_id: str
    hallucination_score: float = Field(
        ge=0.0,
        le=1.0,
        description="0 = well-grounded, 1 = likely hallucinated",
    )
    node_timings: dict[str, float] = Field(
        default_factory=dict,
        description="Per-node latency in milliseconds for observability",
    )


class StreamChunk(BaseModel):
    """
    A single SSE chunk during streaming.
    The client receives these as:
      data: {"type": "token", "content": "Hello"}\n\n
      data: {"type": "sources", "sources": [...]}\n\n
      data: {"type": "done", "metadata": {...}}\n\n
    """

    type: str  # "token" | "sources" | "metadata" | "error" | "done"
    content: str | None = None
    sources: list[SourceReference] | None = None
    metadata: dict[str, Any] | None = None
    error: str | None = None
