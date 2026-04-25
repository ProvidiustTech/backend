"""
app/core/langgraph/graph.py
===========================
RAG Graph: retrieve → rerank → generate → validate → END

Key fixes:
  1. retrieve_node catches ALL errors and returns empty chunks gracefully
     instead of propagating exceptions that cause infinite retry loops
  2. validate_node always passes when there are no chunks (nothing to validate)
  3. route_after_validate skips retry if retrieve_error is set
  4. generate_node gives a helpful message when dimension mismatch occurs
"""

import json
import time
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import (
    rag_hallucination_score,
    rag_latency_seconds,
    rag_requests_total,
    rag_retrieved_chunks,
)
from app.services.llm import get_llm

log = get_logger(__name__)


# ── State ─────────────────────────────────────────────────────────────────────

class SourceChunk(TypedDict):
    text: str
    score: float
    doc_id: str
    doc_title: str
    page: int | None
    metadata: dict[str, Any]


class GraphState(TypedDict):
    query: str
    collection_id: str
    user_id: str
    metadata_filter: dict[str, Any]
    retrieved_chunks: list[SourceChunk]
    reranked_chunks: list[SourceChunk]
    messages: Annotated[list[BaseMessage], add_messages]
    answer: str
    sources: list[SourceChunk]
    hallucination_score: float
    validation_passed: bool
    retry_count: int
    retrieve_error: str        # stores error message if retrieve fails
    node_timings: dict[str, float]


# ── Node 1: Retrieve ──────────────────────────────────────────────────────────

async def retrieve_node(state: GraphState) -> dict:
    """
    Semantic search against pgvector.
    CRITICAL: never raises — always returns, even on error.
    Stores any error in retrieve_error so the router can skip retry.
    """
    t0 = time.perf_counter()
    log.info("retrieve node", query=state["query"][:80], collection_id=state["collection_id"])

    try:
        from app.services.vector_store import get_vector_store

        store = await get_vector_store(state["collection_id"])
        results = await store.asimilarity_search_with_relevance_scores(
            state["query"],
            k=settings.TOP_K_RETRIEVE,
            filter=state.get("metadata_filter") or None,
        )

        chunks: list[SourceChunk] = []
        for doc, score in results:
            if float(score) >= settings.SIMILARITY_THRESHOLD:
                chunks.append(SourceChunk(
                    text=doc.page_content,
                    score=float(score),
                    doc_id=doc.metadata.get("doc_id", ""),
                    doc_title=doc.metadata.get("title", "Unknown"),
                    page=doc.metadata.get("page"),
                    metadata=doc.metadata,
                ))

        elapsed_ms = (time.perf_counter() - t0) * 1000
        rag_retrieved_chunks.labels(collection_id=state["collection_id"]).observe(len(chunks))
        rag_latency_seconds.labels(collection_id=state["collection_id"], node="retrieve").observe(elapsed_ms / 1000)
        log.info("retrieve complete", chunks=len(chunks), elapsed_ms=round(elapsed_ms, 1))

        return {
            "retrieved_chunks": chunks,
            "retrieve_error": "",
            "node_timings": {**state.get("node_timings", {}), "retrieve_ms": elapsed_ms},
        }

    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        error_msg = str(e)
        log.error("retrieve node failed — returning empty", error=error_msg[:200])

        return {
            "retrieved_chunks": [],
            "retrieve_error": error_msg,
            "node_timings": {**state.get("node_timings", {}), "retrieve_ms": elapsed_ms},
        }


# ── Node 2: Rerank ────────────────────────────────────────────────────────────

async def rerank_node(state: GraphState) -> dict:
    t0 = time.perf_counter()
    chunks = state.get("retrieved_chunks", [])

    if not chunks:
        return {
            "reranked_chunks": [],
            "sources": [],
            "node_timings": {**state.get("node_timings", {}), "rerank_ms": 0},
        }

    if settings.COHERE_API_KEY and len(chunks) > settings.RERANK_TOP_N:
        try:
            import cohere
            co = cohere.Client(api_key=settings.COHERE_API_KEY)
            resp = co.rerank(
                model=settings.RERANK_MODEL,
                query=state["query"],
                documents=[c["text"] for c in chunks],
                top_n=settings.RERANK_TOP_N,
            )
            reranked = [
                SourceChunk(**{**chunks[r.index], "score": float(r.relevance_score)})
                for r in resp.results
            ]
        except Exception as e:
            log.warning("cohere rerank failed, using fallback", error=str(e))
            reranked = sorted(chunks, key=lambda c: c["score"], reverse=True)[:settings.RERANK_TOP_N]
    else:
        reranked = sorted(chunks, key=lambda c: c["score"], reverse=True)[:settings.RERANK_TOP_N]

    elapsed_ms = (time.perf_counter() - t0) * 1000
    rag_latency_seconds.labels(collection_id=state["collection_id"], node="rerank").observe(elapsed_ms / 1000)

    return {
        "reranked_chunks": reranked,
        "sources": reranked,
        "node_timings": {**state.get("node_timings", {}), "rerank_ms": elapsed_ms},
    }


# ── Node 3: Generate ──────────────────────────────────────────────────────────

async def generate_node(state: GraphState) -> dict:
    t0 = time.perf_counter()
    chunks = state.get("reranked_chunks", [])
    retrieve_error = state.get("retrieve_error", "")

    # No chunks — give a clear, helpful answer
    if not chunks:
        if "dimension" in retrieve_error.lower():
            answer = (
                "There is a vector dimension mismatch in the database. "
                "This happens when the embedding model changed after documents were indexed. "
                "To fix: delete this collection, create a new one, and re-upload your documents."
            )
        elif retrieve_error:
            answer = (
                "I couldn't search the document collection due to a technical issue. "
                "Please try again in a moment."
            )
        else:
            answer = (
                "I couldn't find relevant information in this collection to answer your question. "
                "Please ensure documents have been uploaded and fully indexed, then try again."
            )

        return {
            "answer": answer,
            "validation_passed": True,   # skip validation — nothing to validate
            "messages": [AIMessage(content=answer)],
            "node_timings": {**state.get("node_timings", {}), "generate_ms": 0},
        }

    context = "\n\n---\n\n".join(
        f"[Source {i}: {c['doc_title']}]\n{c['text']}"
        for i, c in enumerate(chunks, 1)
    )

    from pathlib import Path
    prompt_path = Path(__file__).parent.parent / "prompts" / "system.md"
    system_prompt = (
        prompt_path.read_text()
        if prompt_path.exists()
        else "You are a helpful assistant. Answer based only on the provided context."
    )

    llm = get_llm(streaming=False)

    prompt = (
        f"{system_prompt}\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {state['query']}\n\nAnswer:"
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        answer = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        log.error("LLM generation failed", error=str(e))
        answer = "I encountered an error generating a response. Please try again."

    elapsed_ms = (time.perf_counter() - t0) * 1000
    rag_latency_seconds.labels(collection_id=state["collection_id"], node="generate").observe(elapsed_ms / 1000)
    log.info("generate complete", answer_len=len(answer), elapsed_ms=round(elapsed_ms, 1))

    return {
        "answer": answer,
        "messages": [HumanMessage(content=state["query"]), AIMessage(content=answer)],
        "node_timings": {**state.get("node_timings", {}), "generate_ms": elapsed_ms},
    }


# ── Node 4: Validate ──────────────────────────────────────────────────────────

async def validate_node(state: GraphState) -> dict:
    t0 = time.perf_counter()
    chunks = state.get("reranked_chunks", [])

    # Already marked as passed by generate_node (empty chunks path)
    if state.get("validation_passed") is True:
        return {
            "hallucination_score": 0.0,
            "validation_passed": True,
            "node_timings": {**state.get("node_timings", {}), "validate_ms": 0},
        }

    # No chunks to validate against — pass through
    if not chunks:
        return {
            "hallucination_score": 0.0,
            "validation_passed": True,
            "node_timings": {**state.get("node_timings", {}), "validate_ms": 0},
        }

    from app.core.langgraph.tools import hallucination_check_tool
    result = json.loads(hallucination_check_tool.invoke({
        "answer": state.get("answer", ""),
        "context_chunks": [c["text"] for c in chunks],
    }))
    score = float(result.get("score", 0.0))
    grounded = bool(result.get("grounded", True))

    rag_hallucination_score.observe(score)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    rag_latency_seconds.labels(collection_id=state["collection_id"], node="validate").observe(elapsed_ms / 1000)
    log.info("validate complete", score=score, grounded=grounded)

    # After one retry, always accept to avoid further looping
    if not grounded and state.get("retry_count", 0) >= 1:
        log.warning("hallucination persists after retry — accepting answer")
        grounded = True

    return {
        "hallucination_score": score,
        "validation_passed": grounded,
        "node_timings": {**state.get("node_timings", {}), "validate_ms": elapsed_ms},
    }


# ── Router ────────────────────────────────────────────────────────────────────

def route_after_validate(state: GraphState) -> str:
    """
    Guarantees termination:
    - validation_passed=True          → END
    - retrieve_error is set           → END (retrying won't fix a config error)
    - retry_count >= 1                → END (already retried once)
    - otherwise                       → retrieve (exactly one retry allowed)
    """
    if state.get("validation_passed", True):
        return END

    if state.get("retrieve_error", ""):
        log.info("skipping retry — retrieve had a persistent error")
        return END

    if state.get("retry_count", 0) >= 1:
        log.info("retry limit reached — ending graph")
        return END

    log.info("validation failed — allowing one retry")
    state["retry_count"] = state.get("retry_count", 0) + 1
    return "retrieve"


# ── Graph ─────────────────────────────────────────────────────────────────────

def build_rag_graph():
    g = StateGraph(GraphState)

    g.add_node("retrieve", retrieve_node)
    g.add_node("rerank",   rerank_node)
    g.add_node("generate", generate_node)
    g.add_node("validate", validate_node)

    g.add_edge(START,      "retrieve")
    g.add_edge("retrieve", "rerank")
    g.add_edge("rerank",   "generate")
    g.add_edge("generate", "validate")
    g.add_conditional_edges(
        "validate",
        route_after_validate,
        {END: END, "retrieve": "retrieve"},
    )

    return g.compile()


rag_graph = build_rag_graph()