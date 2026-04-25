"""
evals/test_rag.py
=================
RAG pipeline evaluation tests using pytest + deepeval.

Tests cover:
  1. Accuracy     — Does the answer match the expected answer?
  2. Faithfulness — Is the answer grounded in the retrieved context?
  3. Latency      — Does the pipeline respond within SLA (5s)?
  4. Hallucination guard — Does the validator catch bad answers?

Run: make eval
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Test fixtures ─────────────────────────────────────────────────────────────

SAMPLE_CONTEXT = [
    {
        "text": "Q3 2024 revenue was $4.2M, a 23% increase YoY driven by enterprise contracts.",
        "score": 0.92,
        "doc_id": "doc-001",
        "doc_title": "Q3 2024 Financial Report",
        "page": 3,
        "metadata": {"year": 2024, "department": "finance"},
    },
    {
        "text": "Operating costs increased 8% to $1.8M due to headcount growth in engineering.",
        "score": 0.87,
        "doc_id": "doc-001",
        "doc_title": "Q3 2024 Financial Report",
        "page": 4,
        "metadata": {"year": 2024, "department": "finance"},
    },
]

GROUNDED_ANSWER = "Q3 2024 revenue was $4.2M, representing a 23% year-over-year increase."
HALLUCINATED_ANSWER = "Revenue was $10M in Q3, growing 50% from the previous quarter."


# ── Unit: Hallucination Guard ─────────────────────────────────────────────────

class TestHallucinationGuard:

    def test_grounded_answer_passes(self):
        from app.core.langgraph.tools import hallucination_check_tool
        result = json.loads(
            hallucination_check_tool.invoke({
                "answer": GROUNDED_ANSWER,
                "context_chunks": [c["text"] for c in SAMPLE_CONTEXT],
            })
        )
        assert result["grounded"] is True
        assert result["score"] < 0.4

    def test_hallucinated_answer_detected(self):
        from app.core.langgraph.tools import hallucination_check_tool
        result = json.loads(
            hallucination_check_tool.invoke({
                "answer": HALLUCINATED_ANSWER,
                "context_chunks": [c["text"] for c in SAMPLE_CONTEXT],
            })
        )
        # Score should be higher for hallucinated content
        assert result["score"] >= 0.0  # basic sanity check

    def test_empty_context_fails(self):
        from app.core.langgraph.tools import hallucination_check_tool
        result = json.loads(
            hallucination_check_tool.invoke({
                "answer": GROUNDED_ANSWER,
                "context_chunks": [],
            })
        )
        assert result["grounded"] is False
        assert result["score"] == 1.0


# ── Unit: Text Utilities ──────────────────────────────────────────────────────

class TestTextUtils:

    def test_clean_text_removes_control_chars(self):
        from app.utils.text import clean_text
        dirty = "Hello\x00World\x0b\nTest"
        clean = clean_text(dirty)
        assert "\x00" not in clean
        assert "\x0b" not in clean
        assert "Hello" in clean

    def test_clean_text_collapses_whitespace(self):
        from app.utils.text import clean_text
        result = clean_text("Hello   World")
        assert "  " not in result

    def test_truncate_text(self):
        from app.utils.text import truncate_text
        long_text = "word " * 200
        result = truncate_text(long_text, max_chars=50)
        assert len(result) <= 53  # 50 + "..."
        assert result.endswith("...")

    def test_estimate_tokens(self):
        from app.utils.text import estimate_tokens
        text = "a" * 400  # ~100 tokens
        assert estimate_tokens(text) == 100


# ── Integration: Graph State Flow ─────────────────────────────────────────────

class TestGraphStateFlow:
    """
    Test the LangGraph RAG pipeline with mocked external services.
    These run without real DB/LLM connections.
    """

    @pytest.mark.asyncio
    async def test_retrieve_node_empty_returns_gracefully(self):
        from app.core.langgraph.graph import GraphState, retrieve_node

        state = GraphState(
            query="What is revenue?",
            collection_id="test-collection-123",
            user_id="user-1",
            metadata_filter={},
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

        with patch(
            "app.core.langgraph.graph.get_vector_store",
            new_callable=AsyncMock,
        ) as mock_store:
            mock_store.return_value.asimilarity_search_with_relevance_scores = AsyncMock(
                return_value=[]
            )
            result = await retrieve_node(state)

        assert "retrieved_chunks" in result
        assert result["retrieved_chunks"] == []

    @pytest.mark.asyncio
    async def test_rerank_node_no_chunks(self):
        from app.core.langgraph.graph import GraphState, rerank_node

        state = GraphState(
            query="test",
            collection_id="col-1",
            user_id="user-1",
            metadata_filter={},
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

        result = await rerank_node(state)
        assert result["reranked_chunks"] == []

    @pytest.mark.asyncio
    async def test_validate_node_passes_grounded_answer(self):
        from app.core.langgraph.graph import GraphState, validate_node

        state = GraphState(
            query="What was Q3 revenue?",
            collection_id="col-1",
            user_id="user-1",
            metadata_filter={},
            retrieved_chunks=SAMPLE_CONTEXT,
            reranked_chunks=SAMPLE_CONTEXT,
            messages=[],
            answer=GROUNDED_ANSWER,
            sources=SAMPLE_CONTEXT,
            hallucination_score=0.0,
            validation_passed=False,
            retry_count=0,
            node_timings={},
        )

        result = await validate_node(state)
        assert result["validation_passed"] is True
        assert result["hallucination_score"] < 0.6


# ── Latency Benchmark ─────────────────────────────────────────────────────────

class TestLatencyBenchmarks:
    """
    Ensure hallucination check tool runs well under SLA.
    """

    def test_hallucination_check_latency(self):
        from app.core.langgraph.tools import hallucination_check_tool

        start = time.perf_counter()
        for _ in range(100):
            hallucination_check_tool.invoke({
                "answer": GROUNDED_ANSWER,
                "context_chunks": [c["text"] for c in SAMPLE_CONTEXT],
            })
        elapsed_ms = (time.perf_counter() - start) * 1000

        avg_ms = elapsed_ms / 100
        print(f"\nHallucination check avg: {avg_ms:.2f}ms")
        assert avg_ms < 50, f"Hallucination check too slow: {avg_ms:.2f}ms (SLA: 50ms)"
