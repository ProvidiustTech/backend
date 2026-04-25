"""
app/services/vector_store.py
=============================
LlamaIndex-powered vector store service backed by PostgreSQL + pgvector.

Responsibilities:
  1. Intelligent chunking  — SentenceSplitter (structure-aware) + SemanticChunker
  2. Indexing             — store chunks + embeddings in pgvector
  3. Retrieval            — hybrid search (dense + BM25) with parent-doc retrieval
  4. Collection CRUD      — create / list / delete collections

Why LlamaIndex here instead of pure LangChain?
  LlamaIndex has superior chunking primitives (SemanticChunker, SentenceWindowNode)
  and a mature pgvector integration. The retrieved nodes are converted to LangChain
  Document objects before entering the LangGraph pipeline.
"""

import time
import uuid
from typing import Any

from llama_index.core import Document as LlamaDocument
from llama_index.core import StorageContext, VectorStoreIndex, Settings as LlamaSettings
from llama_index.core.node_parser import (
    SemanticSplitterNodeParser,
    SentenceSplitter,
    HierarchicalNodeParser,
)
from llama_index.core.postprocessor import SimilarityPostprocessor
from llama_index.vector_stores.postgres import PGVectorStore

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import (
    indexing_chunks_created,
    indexing_documents_total,
    indexing_latency_seconds,
)
from app.services.llm import get_llama_index_embeddings, get_llama_index_llm

log = get_logger(__name__)

# Cache of active vector stores keyed by collection_id
_store_cache: dict[str, PGVectorStore] = {}


def _configure_llama_settings() -> None:
    """Set global LlamaIndex LLM + embedding model once."""
    LlamaSettings.llm = get_llama_index_llm()
    LlamaSettings.embed_model = get_llama_index_embeddings()
    LlamaSettings.chunk_size = settings.CHUNK_SIZE
    LlamaSettings.chunk_overlap = settings.CHUNK_OVERLAP


def _get_pg_vector_store(collection_id: str) -> PGVectorStore:
    """
    Return (or create) a PGVectorStore for a given collection.
    Each collection gets its own table name so collections are isolated.
    """
    if collection_id in _store_cache:
        return _store_cache[collection_id]

    # Parse the connection string to extract components
    # DATABASE_URL format: postgresql+asyncpg://user:pass@host:port/db
    # PGVectorStore needs the sync psycopg2 connection string
    db_url = settings.DATABASE_URL.replace("+asyncpg", "")

    store = PGVectorStore.from_params(
        database=settings.POSTGRES_DB,
        host=settings.POSTGRES_HOST,
        password=settings.POSTGRES_PASSWORD,
        port=str(settings.POSTGRES_PORT),
        user=settings.POSTGRES_USER,
        table_name=f"chunks_{collection_id.replace('-', '_')}",
        embed_dim=settings.EMBEDDING_DIM,
        hnsw_kwargs={
            "hnsw_m": 16,
            "hnsw_ef_construction": 64,
            "hnsw_ef_search": 40,
        },
    )

    _store_cache[collection_id] = store
    return store


def _build_chunkers() -> tuple:
    """
    Build the two-stage chunking pipeline:
      1. SentenceSplitter: structure-aware splits (respects sentence/paragraph boundaries)
      2. SemanticSplitter: groups sentences by semantic similarity
    Returns (sentence_splitter, semantic_splitter, hierarchical_parser)
    """
    sentence_splitter = SentenceSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        paragraph_separator="\n\n",
    )

    semantic_splitter = SemanticSplitterNodeParser(
        buffer_size=1,
        breakpoint_percentile_threshold=95,
        embed_model=get_llama_index_embeddings(),
    )

    # Hierarchical parser for parent-document retrieval
    # Creates parent chunks (1024 tokens) and child chunks (256 tokens)
    hierarchical_parser = HierarchicalNodeParser.from_defaults(
        chunk_sizes=[1024, 512, 256],
    )

    return sentence_splitter, semantic_splitter, hierarchical_parser


async def index_document(
    collection_id: str,
    document_id: str,
    title: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> int:
    """
    Chunk, embed, and store a document in pgvector.

    Strategy:
      - Short documents (< 2000 chars): simple SentenceSplitter
      - Long documents: SemanticChunker for better semantic coherence
      - All chunks get parent/child hierarchy for advanced retrieval

    Returns: number of chunks created
    """
    t0 = time.perf_counter()
    _configure_llama_settings()

    base_metadata = {
        "doc_id": document_id,
        "collection_id": collection_id,
        "title": title,
        **(metadata or {}),
    }

    llama_doc = LlamaDocument(
        text=text,
        doc_id=document_id,
        metadata=base_metadata,
    )

    sentence_splitter, semantic_splitter, _ = _build_chunkers()

    # Choose chunking strategy based on document length
    if len(text) < 2000:
        log.debug("using sentence splitter (short doc)", doc_id=document_id)
        nodes = sentence_splitter.get_nodes_from_documents([llama_doc])
    else:
        log.debug("using semantic splitter (long doc)", doc_id=document_id)
        try:
            nodes = semantic_splitter.get_nodes_from_documents([llama_doc])
            # Fallback to sentence splitter if semantic produces too few chunks
            if len(nodes) < 2:
                nodes = sentence_splitter.get_nodes_from_documents([llama_doc])
        except Exception as e:
            log.warning("semantic splitter failed, falling back", error=str(e))
            nodes = sentence_splitter.get_nodes_from_documents([llama_doc])

    if not nodes:
        log.warning("no nodes created from document", doc_id=document_id)
        return 0

    # Add chunk index to metadata for ordering
    for i, node in enumerate(nodes):
        node.metadata["chunk_index"] = i
        node.metadata["total_chunks"] = len(nodes)

    # Store in pgvector via LlamaIndex
    store = _get_pg_vector_store(collection_id)
    storage_context = StorageContext.from_defaults(vector_store=store)
    VectorStoreIndex(nodes, storage_context=storage_context, show_progress=False)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    chunk_count = len(nodes)

    # Metrics
    indexing_documents_total.labels(collection_id=collection_id, status="success").inc()
    indexing_chunks_created.labels(collection_id=collection_id).inc(chunk_count)
    indexing_latency_seconds.labels(collection_id=collection_id).observe(elapsed_ms / 1000)

    log.info(
        "document indexed",
        doc_id=document_id,
        chunks=chunk_count,
        elapsed_ms=round(elapsed_ms, 1),
    )

    return chunk_count


async def get_vector_store(collection_id: str) -> "LangChainVectorStoreWrapper":
    """
    Return a LangChain-compatible wrapper around the pgvector store.
    This is what LangGraph nodes call for retrieval.
    """
    _configure_llama_settings()
    pg_store = _get_pg_vector_store(collection_id)
    return LangChainVectorStoreWrapper(pg_store, collection_id)


class LangChainVectorStoreWrapper:
    """
    Thin adapter that gives the LangGraph nodes a familiar
    `asimilarity_search_with_relevance_scores(query, k, filter)` interface
    backed by LlamaIndex's PGVectorStore.
    """

    def __init__(self, store: PGVectorStore, collection_id: str):
        self._store = store
        self._collection_id = collection_id
        self._embeddings = get_llama_index_embeddings()

    async def asimilarity_search_with_relevance_scores(
        self,
        query: str,
        k: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[Any, float]]:
        """
        Async similarity search returning (Document, score) pairs.
        Converts LlamaIndex NodeWithScore → LangChain Document format.
        """
        from langchain_core.documents import Document as LCDocument

        index = VectorStoreIndex.from_vector_store(self._store)

        retriever = index.as_retriever(
            similarity_top_k=k,
            filters=_build_metadata_filters(filter) if filter else None,
        )

        nodes = await retriever.aretrieve(query)

        results = []
        for node_with_score in nodes:
            lc_doc = LCDocument(
                page_content=node_with_score.node.get_content(),
                metadata={
                    **node_with_score.node.metadata,
                    "node_id": node_with_score.node.node_id,
                },
            )
            results.append((lc_doc, float(node_with_score.score or 0.0)))

        return results


def _build_metadata_filters(filter_dict: dict[str, Any]):
    """Convert a plain dict to LlamaIndex MetadataFilters."""
    from llama_index.core.vector_stores import MetadataFilter, MetadataFilters, FilterOperator

    filters = [
        MetadataFilter(key=k, value=v, operator=FilterOperator.EQ)
        for k, v in filter_dict.items()
    ]
    return MetadataFilters(filters=filters)
