"""
app/core/metrics.py
===================
Custom Prometheus metrics for the IntegrateAI Blueprint.
Mounted at /metrics by prometheus-fastapi-instrumentator.

Design: one authoritative registry here; import individual metrics
where needed rather than calling prometheus_client directly.

Dashboards live in grafana/dashboards/integrateai.json
"""

from prometheus_client import Counter, Gauge, Histogram, Summary

# ── RAG Pipeline Metrics ──────────────────────────────────────────────────────

rag_requests_total = Counter(
    "integrateai_rag_requests_total",
    "Total RAG pipeline requests",
    labelnames=["collection_id", "provider", "status"],
)

rag_latency_seconds = Histogram(
    "integrateai_rag_latency_seconds",
    "End-to-end RAG pipeline latency in seconds",
    labelnames=["collection_id", "node"],  # node = retrieve|rerank|generate|validate
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

rag_tokens_used = Counter(
    "integrateai_rag_tokens_used_total",
    "LLM tokens consumed",
    labelnames=["provider", "model", "type"],  # type = prompt|completion
)

rag_retrieved_chunks = Histogram(
    "integrateai_rag_retrieved_chunks",
    "Number of chunks retrieved before reranking",
    labelnames=["collection_id"],
    buckets=(1, 2, 5, 10, 20, 50),
)

rag_hallucination_score = Summary(
    "integrateai_rag_hallucination_score",
    "Hallucination guard score (0 = no hallucination, 1 = likely hallucinated)",
)

# ── Document Indexing Metrics ─────────────────────────────────────────────────

indexing_documents_total = Counter(
    "integrateai_indexing_documents_total",
    "Total documents indexed",
    labelnames=["collection_id", "status"],
)

indexing_chunks_created = Counter(
    "integrateai_indexing_chunks_created_total",
    "Total chunks/nodes created during indexing",
    labelnames=["collection_id"],
)

indexing_latency_seconds = Histogram(
    "integrateai_indexing_latency_seconds",
    "Document indexing latency",
    labelnames=["collection_id"],
    buckets=(0.5, 1.0, 2.0, 5.0, 15.0, 60.0),
)

# ── Auth Metrics ──────────────────────────────────────────────────────────────

auth_attempts_total = Counter(
    "integrateai_auth_attempts_total",
    "Total authentication attempts",
    labelnames=["endpoint", "status"],  # status = success|failure
)

# ── System Health ─────────────────────────────────────────────────────────────

active_collections = Gauge(
    "integrateai_active_collections",
    "Number of active document collections",
)

vector_store_size = Gauge(
    "integrateai_vector_store_size_chunks",
    "Total number of chunks stored in pgvector",
    labelnames=["collection_id"],
)
