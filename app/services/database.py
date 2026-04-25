"""
app/services/database.py
========================
Async database layer using SQLAlchemy 2.0 + asyncpg.
Includes:
  - Async engine and session factory
  - Base declarative model
  - pgvector extension bootstrap
  - Health-check helper

The actual ORM models (Document, Collection, User, etc.) live in app/models/.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# ── SQLAlchemy engine ─────────────────────────────────────────────────────────

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,            # log SQL in dev
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,             # detect stale connections
    pool_recycle=3600,              # recycle connections every hour
)

# Session factory — use as an async context manager
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,         # avoid lazy-load issues after commit
    autoflush=False,
)

# ── Declarative base ──────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    """
    All ORM models inherit from this.
    SQLAlchemy 2.0 style: no metadata arguments needed.
    """
    pass


# ── Dependency for FastAPI routes ─────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a database session.

    Usage in a route:
        @router.get("/things")
        async def list_things(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Context-manager variant (for services / background tasks) ─────────────────

@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager for use outside of FastAPI's DI.

    Usage:
        async with get_db_context() as db:
            result = await db.execute(...)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Startup / teardown ────────────────────────────────────────────────────────

async def init_db() -> None:
    """
    Called once at application startup.
    - Enables pgvector extension
    - Creates all tables (dev only; use Alembic migrations in production)
    """
    async with engine.begin() as conn:
        # Enable pgvector
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        log.info("pgvector extension ready")

        # Import models so their metadata is registered with Base
        from app.models import collection, document, user  # noqa: F401

        # In development, auto-create tables. In production, run: make migrate
        if settings.ENVIRONMENT == "development":
            await conn.run_sync(Base.metadata.create_all)
            log.info("database tables created (dev mode)")


async def close_db() -> None:
    """Gracefully close the connection pool on shutdown."""
    await engine.dispose()
    log.info("database connection pool closed")


async def check_db_health() -> dict:
    """
    Lightweight health-check used by GET /health.
    Returns {"status": "ok", "latency_ms": float} or raises.
    """
    import time

    start = time.perf_counter()
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    return {"status": "ok", "latency_ms": latency_ms}
