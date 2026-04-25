"""
app/core/langgraph/tools.py
===========================
Custom LangChain tools used by LangGraph nodes.

Current tools:
  - VectorSearchTool   : semantic + hybrid search against pgvector
  - MetadataFilterTool : filter documents by metadata fields
  - HallucinationCheckTool : cross-check answer against retrieved context

Adding new tools (Agentic Pack):
  - WebSearchTool, CalculatorTool, SQLQueryTool, etc.
  Simply define them here and register in graph.py's ToolNode.
"""

import json
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


# ── Input schemas (Pydantic v2) ───────────────────────────────────────────────

class VectorSearchInput(BaseModel):
    query: str = Field(description="The search query to embed and retrieve against")
    collection_id: str = Field(description="UUID of the document collection to search")
    top_k: int = Field(default=10, description="Number of results to retrieve")
    metadata_filter: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata key-value pairs to filter results",
    )


class HallucinationCheckInput(BaseModel):
    answer: str = Field(description="The generated answer to verify")
    context_chunks: list[str] = Field(description="The retrieved source chunks")


# ── Tool implementations ──────────────────────────────────────────────────────

@tool(args_schema=VectorSearchInput)
async def vector_search_tool(
    query: str,
    collection_id: str,
    top_k: int = 10,
    metadata_filter: dict[str, Any] | None = None,
) -> str:
    """
    Search a document collection using semantic (dense) search.
    Returns a JSON list of {text, score, metadata} objects.
    """
    from app.services.vector_store import get_vector_store

    log.debug("vector_search_tool called", query=query[:80], collection_id=collection_id)

    try:
        store = await get_vector_store(collection_id)
        results = await store.asimilarity_search_with_relevance_scores(
            query,
            k=top_k,
            filter=metadata_filter,
        )

        output = [
            {
                "text": doc.page_content,
                "score": float(score),
                "metadata": doc.metadata,
            }
            for doc, score in results
            if float(score) >= settings.SIMILARITY_THRESHOLD
        ]

        log.debug("vector_search_tool results", count=len(output))
        return json.dumps(output)

    except Exception as e:
        log.error("vector_search_tool failed", error=str(e))
        return json.dumps({"error": str(e)})


@tool(args_schema=HallucinationCheckInput)
def hallucination_check_tool(answer: str, context_chunks: list[str]) -> str:
    """
    Lightweight hallucination guard.
    Checks whether key claims in the answer are grounded in the context.

    Returns JSON: {"score": float, "grounded": bool, "warnings": list[str]}

    Score interpretation:
      0.0 – 0.3 : Well-grounded, low hallucination risk
      0.3 – 0.6 : Partially grounded, review suggested
      0.6 – 1.0 : Likely hallucinated, should not be returned to user
    """
    if not context_chunks:
        return json.dumps({
            "score": 1.0,
            "grounded": False,
            "warnings": ["No context provided — cannot verify answer."],
        })

    # Build a combined context string for overlap analysis
    combined_context = " ".join(context_chunks).lower()
    answer_lower = answer.lower()

    # Heuristic: split answer into sentences, check how many are grounded
    import re
    sentences = [s.strip() for s in re.split(r"[.!?]", answer_lower) if len(s.strip()) > 20]

    if not sentences:
        return json.dumps({"score": 0.0, "grounded": True, "warnings": []})

    ungrounded_sentences = []
    for sentence in sentences:
        # Check if key noun phrases appear in context (simple overlap check)
        words = set(sentence.split())
        context_words = set(combined_context.split())
        overlap = len(words & context_words) / max(len(words), 1)

        if overlap < 0.25:  # less than 25% word overlap
            ungrounded_sentences.append(sentence[:100])

    score = len(ungrounded_sentences) / len(sentences)
    grounded = score < 0.4

    warnings = []
    if ungrounded_sentences:
        warnings = [f"Potentially ungrounded: '{s}...'" for s in ungrounded_sentences[:3]]

    return json.dumps({
        "score": round(score, 3),
        "grounded": grounded,
        "warnings": warnings,
    })


# ── Tool registry (import this in graph.py) ───────────────────────────────────

RAG_TOOLS = [vector_search_tool, hallucination_check_tool]
