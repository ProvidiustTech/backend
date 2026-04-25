"""
app/services/llm.py
===================
Multi-provider LLM factory.
Supports: OpenAI, Anthropic, Groq, Ollama (local/free).

Switch via LLM_PROVIDER in .env — no code changes needed.
Ollama requires `ollama serve` running locally (brew install ollama).
"""

from functools import lru_cache
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.embeddings import Embeddings

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


@lru_cache(maxsize=1)
def get_llm(
    provider: str | None = None,
    temperature: float = 0.1,
    streaming: bool = True,
) -> BaseChatModel:
    """
    Return a LangChain chat model for the active provider.

    openai    — GPT-4o-mini (paid)
    anthropic — Claude Haiku (paid)
    groq      — Llama 3.1 70B (free tier, ~300 tok/s)
    ollama    — Llama 3.2 local (free, no internet after pull)
    """
    active = provider or settings.LLM_PROVIDER
    model_name = settings.active_llm_model

    log.info("initialising LLM", provider=active, model=model_name)

    if active == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_name,
            api_key=settings.OPENAI_API_KEY,
            temperature=temperature,
            streaming=streaming,
            max_retries=3,
        )

    elif active == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(  # type: ignore[call-arg]
            model=model_name,
            api_key=settings.ANTHROPIC_API_KEY,
            temperature=temperature,
            streaming=streaming,
            max_retries=3,
        )

    elif active == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=model_name,
            api_key=settings.GROQ_API_KEY,
            temperature=temperature,
            streaming=streaming,
        )

    elif active == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            raise ImportError(
                "langchain-ollama not installed. Run: uv add langchain-ollama"
            )
        return ChatOllama(
            model=model_name,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=temperature,
        )

    else:
        raise ValueError(
            f"Unsupported LLM_PROVIDER: '{active}'. "
            "Choose: openai | anthropic | groq | ollama"
        )


@lru_cache(maxsize=1)
def get_embeddings() -> Embeddings:
    """
    Return the embeddings model.

    ollama  → nomic-embed-text (free, local, pull once)
    others  → OpenAI text-embedding-3-small (requires OPENAI_API_KEY)

    Changing this model requires re-indexing all documents in pgvector.
    """
    if settings.LLM_PROVIDER == "ollama":
        try:
            from langchain_ollama import OllamaEmbeddings
        except ImportError:
            raise ImportError(
                "langchain-ollama not installed. Run: uv add langchain-ollama"
            )
        log.info("using Ollama embeddings", model="nomic-embed-text")
        return OllamaEmbeddings(
            model="nomic-embed-text",
            base_url=settings.OLLAMA_BASE_URL,
        )

    if not settings.OPENAI_API_KEY:
        log.warning(
            "OPENAI_API_KEY not set — embeddings will fail. "
            "Use LLM_PROVIDER=ollama for free local embeddings."
        )
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(
        model=settings.OPENAI_EMBEDDING_MODEL,
        api_key=settings.OPENAI_API_KEY,
    )


def get_llama_index_llm() -> Any:
    """LlamaIndex-compatible LLM (used by SemanticChunker in vector_store)."""
    active = settings.LLM_PROVIDER

    if active == "ollama":
        try:
            from llama_index.llms.ollama import Ollama
            return Ollama(
                model=settings.OLLAMA_MODEL,
                base_url=settings.OLLAMA_BASE_URL,
                request_timeout=120.0,
            )
        except ImportError:
            raise ImportError("Run: uv add llama-index-llms-ollama")

    elif active == "openai":
        from llama_index.llms.openai import OpenAI
        return OpenAI(model=settings.OPENAI_MODEL, api_key=settings.OPENAI_API_KEY)

    elif active == "anthropic":
        from llama_index.llms.anthropic import Anthropic
        return Anthropic(model=settings.ANTHROPIC_MODEL, api_key=settings.ANTHROPIC_API_KEY)

    else:
        # Groq via OpenAI-compatible endpoint
        from llama_index.llms.openai import OpenAI
        return OpenAI(
            model=settings.GROQ_MODEL,
            api_key=settings.GROQ_API_KEY,
            api_base="https://api.groq.com/openai/v1",
        )


def get_llama_index_embeddings() -> Any:
    """LlamaIndex-compatible embeddings wrapper."""
    if settings.LLM_PROVIDER == "ollama":
        try:
            from llama_index.embeddings.ollama import OllamaEmbedding
            return OllamaEmbedding(
                model_name="nomic-embed-text",
                base_url=settings.OLLAMA_BASE_URL,
            )
        except ImportError:
            raise ImportError("Run: uv add llama-index-embeddings-ollama")

    from llama_index.embeddings.openai import OpenAIEmbedding
    return OpenAIEmbedding(
        model=settings.OPENAI_EMBEDDING_MODEL,
        api_key=settings.OPENAI_API_KEY,
    )