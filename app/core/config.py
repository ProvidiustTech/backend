"""
app/core/config.py
==================
Central configuration using Pydantic v2 BaseSettings.

Changes:
  - SECRET_KEY and DATABASE_URL have safe dev defaults
  - LLM_PROVIDER now accepts 'ollama'
  - OLLAMA_MODEL and OLLAMA_BASE_URL added
  - API key validation only enforced in production
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    
    
    # Embedding dimensions — must match the model being used
    # nomic-embed-text (Ollama) = 768
    # text-embedding-3-small (OpenAI) = 1536
    EMBEDDING_DIM: int = 768  # set to 1536 if using OpenAI

    # ── App ───────────────────────────────────────────────────────────────────
    APP_NAME: str = "Providius"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = True

    # Safe defaults — production MUST override via real env vars
    SECRET_KEY: str = Field(
        default="dev-secret-key-change-in-production-min32ch",
        min_length=32,
    )

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://integrateai:integrateai_secret@localhost:5432/integrateai_db"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "integrateai"
    POSTGRES_PASSWORD: str = "integrateai_secret"
    POSTGRES_DB: str = "integrateai_db"

    # ── LLM Providers ─────────────────────────────────────────────────────────
    LLM_PROVIDER: Literal["openai", "anthropic", "groq", "ollama"] = "ollama"

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-haiku-20241022"

    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.1-70b-versatile"

    # ── Ollama (local, free, no API key needed) ───────────────────────────────
    OLLAMA_MODEL: str = "llama3.2"
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # ── Cohere reranking ──────────────────────────────────────────────────────
    COHERE_API_KEY: str = ""
    RERANK_MODEL: str = "rerank-english-v3.0"
    RERANK_TOP_N: int = 5

    # ── JWT ───────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = "dev-jwt-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_CHAT_PER_MINUTE: int = 20

    # ── RAG ───────────────────────────────────────────────────────────────────
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    TOP_K_RETRIEVE: int = 10
    TOP_K_FINAL: int = 5
    SIMILARITY_THRESHOLD: float = 0.7
    ENABLE_HYBRID_SEARCH: bool = True

    # ── Monitoring ────────────────────────────────────────────────────────────
    PROMETHEUS_ENABLED: bool = True
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "console"

    GRAFANA_USER: str = "admin"
    GRAFANA_PASSWORD: str = "integrateai_grafana"

    # ── Computed ──────────────────────────────────────────────────────────────

    @computed_field  # type: ignore[misc]
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @computed_field  # type: ignore[misc]
    @property
    def active_llm_model(self) -> str:
        return {
            "openai":    self.OPENAI_MODEL,
            "anthropic": self.ANTHROPIC_MODEL,
            "groq":      self.GROQ_MODEL,
            "ollama":    self.OLLAMA_MODEL,
        }[self.LLM_PROVIDER]

    @model_validator(mode="after")
    def validate_llm_keys(self) -> "Settings":
        # Only enforce API key presence in production. Ollama never needs one.
        if self.ENVIRONMENT == "production" and self.LLM_PROVIDER != "ollama":
            key_map = {
                "openai":    self.OPENAI_API_KEY,
                "anthropic": self.ANTHROPIC_API_KEY,
                "groq":      self.GROQ_API_KEY,
            }
            if not key_map.get(self.LLM_PROVIDER, ""):
                raise ValueError(
                    f"LLM_PROVIDER='{self.LLM_PROVIDER}' but the API key is not set."
                )
        return self

    @computed_field  # type: ignore[misc]
    @property
    def cors_origins(self) -> list[str]:
        if self.ENVIRONMENT == "development":
            return ["*"]
        return ["https://app.providius.io"]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()