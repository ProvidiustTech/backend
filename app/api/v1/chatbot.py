"""
app/api/v1/chatbot.py
=====================
The /chat endpoint — generic RAG chat against a document collection.

Fixes:
  - Passes recursion_limit=25 to graph invocation to prevent infinite loops
  - Simplified state merging — no longer tries to mutate state mid-stream
  - ainvoke used instead of astream for reliability
  - Streaming simulated from the final answer (real LLM streaming
    requires LangGraph streaming mode which needs a checkpointer)
"""

import json
import uuid
from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_current_user
from app.core.langgraph.graph import GraphState, rag_graph
from app.core.logging import get_logger
from app.core.metrics import rag_requests_total
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse, SourceReference, StreamChunk
from app.services.database import get_db

log = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])

# LangGraph recursion limit — 25 steps covers 2 full passes with headroom
GRAPH_CONFIG = {"recursion_limit": 25}


def _chunks_to_sources(chunks: list) -> list[SourceReference]:
    """Convert GraphState source chunks to API SourceReference objects."""
    sources = []
    for c in (chunks or [])[:5]:
        sources.append(
            SourceReference(
                doc_id=c.get("doc_id", ""),
                doc_title=c.get("doc_title", "Unknown"),
                page=c.get("page"),
                relevance_score=min(float(c.get("score", 0.0)), 1.0),
                excerpt=c.get("text", "")[:300],
            )
        )
    return sources


def _build_initial_state(request: ChatRequest, user_id: str) -> GraphState:
    """Build a clean initial GraphState from the API request."""
    return GraphState(
        query=request.query,
        collection_id=str(request.collection_id),
        user_id=user_id,
        metadata_filter=request.metadata_filter or {},
        retrieved_chunks=[],
        reranked_chunks=[],
        messages=[],
        answer="",
        sources=[],
        hallucination_score=0.0,
        validation_passed=False,
        retry_count=0,
        node_timings={},
    )


async def _run_rag(request: ChatRequest, user_id: str) -> GraphState:
    """Run the RAG graph and return the final state."""
    initial_state = _build_initial_state(request, user_id)
    try:
        # ainvoke runs the full graph synchronously and returns final state
        final_state = await rag_graph.ainvoke(initial_state, config=GRAPH_CONFIG)
        return final_state
    except Exception as e:
        log.exception("RAG graph failed", error=str(e))
        raise


async def _stream_rag(request: ChatRequest, user_id: str) -> AsyncGenerator[str, None]:
    """
    Run the RAG graph then stream the answer word-by-word as SSE events.

    SSE event types:
      {"type": "token",   "content": "Hello "}
      {"type": "sources", "sources": [...]}
      {"type": "done",    "metadata": {...}}
      {"type": "error",   "error": "..."}
    """

    def sse(chunk: StreamChunk) -> str:
        return f"data: {chunk.model_dump_json()}\n\n"

    try:
        final_state = await _run_rag(request, user_id)

        answer = final_state.get("answer", "")
        sources = final_state.get("sources") or final_state.get("reranked_chunks", [])

        # Send sources first so client can display them while answer streams
        if sources:
            yield sse(StreamChunk(
                type="sources",
                sources=_chunks_to_sources(sources),
            ))

        # Stream answer word by word
        words = answer.split(" ")
        for i, word in enumerate(words):
            token = word + (" " if i < len(words) - 1 else "")
            yield sse(StreamChunk(type="token", content=token))

        # Final metadata event
        yield sse(StreamChunk(
            type="done",
            metadata={
                "hallucination_score": final_state.get("hallucination_score", 0.0),
                "validation_passed":   final_state.get("validation_passed", True),
                "node_timings":        final_state.get("node_timings", {}),
                "collection_id":       str(request.collection_id),
                "retry_count":         final_state.get("retry_count", 0),
            },
        ))

        rag_requests_total.labels(
            collection_id=str(request.collection_id),
            provider=__import__("app.core.config", fromlist=["settings"]).settings.LLM_PROVIDER,
            status="success",
        ).inc()

    except Exception as e:
        log.exception("RAG stream failed", error=str(e))
        rag_requests_total.labels(
            collection_id=str(request.collection_id),
            provider="unknown",
            status="error",
        ).inc()
        yield sse(StreamChunk(type="error", error=str(e)))


@router.post(
    "",
    summary="Chat with a document collection",
    description=(
        "Send a query and receive a grounded answer from the RAG pipeline. "
        "Supports streaming (SSE) and non-streaming JSON responses."
    ),
)
async def chat(
    request: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """
    Main chat endpoint.

    **Streaming (stream=true)**: Returns SSE stream.
    Events: token | sources | done | error

    **Non-streaming (stream=false)**: Returns ChatResponse JSON.
    """
    log.info(
        "chat request",
        user_id=str(current_user.id),
        collection_id=str(request.collection_id),
        query_len=len(request.query),
        stream=request.stream,
    )

    if request.stream:
        return StreamingResponse(
            _stream_rag(request, str(current_user.id)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming
    try:
        final_state = await _run_rag(request, str(current_user.id))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"RAG pipeline error: {str(e)}",
        )

    sources = final_state.get("sources") or final_state.get("reranked_chunks", [])

    return ChatResponse(
        answer=final_state.get("answer", ""),
        sources=_chunks_to_sources(sources),
        conversation_id=str(request.conversation_id or uuid.uuid4()),
        collection_id=str(request.collection_id),
        hallucination_score=final_state.get("hallucination_score", 0.0),
        node_timings=final_state.get("node_timings", {}),
    )