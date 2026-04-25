"""
app/main.py
===========
Providius FastAPI application entry point.

Agent priority:
  PRIMARY   — Customer Service Agent (web-scrape powered)
  SECONDARY — Social Media Agent

Routes:
  /              → PWA landing page
  /docs          → Swagger UI
  /api/v1/auth/  → JWT auth
  /api/v1/chat/  → Generic RAG chat
  /api/v1/agents/cs/*     → CS Agent (primary)
  /api/v1/agents/social/* → Social Agent (secondary)
  /metrics       → Prometheus
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.v1 import auth, chatbot, endpoints
from app.api.v1 import agents as agents_router
from app.api.v1 import frontend as frontend_router
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.middleware import register_middleware
from app.services.database import close_db, init_db, engine, Base

log = get_logger(__name__)
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
limiter = Limiter(key_func=get_remote_address,
                  default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log.info("starting Providius", version=settings.APP_VERSION,
             environment=settings.ENVIRONMENT, llm=settings.LLM_PROVIDER)

    # Register all models before creating tables
    from app.models import (  # noqa: F401
        User, Collection, Document, DocumentChunk,
        SocialProfile, SocialPost, SocialSchedule,
        CompanyRegistration, CSSession, CSMessage, Escalation,
    )

    await init_db()

    # Create any new tables added since last run
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    log.info("all database tables ready")
    yield
    log.info("shutting down Providius")
    await close_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Providius — AI Agents API",
        version=settings.APP_VERSION,
        description=(
            "**Providius** — Customer Service + Social Media AI Agents.\n\n"
            "**Primary**: CS Agent — web-scrape powered, grounded in your company knowledge.\n\n"
            "**Secondary**: Social Media Agent — trend-aware post composition.\n\n"
            "Landing page: `/` | Docs: `/docs` | Auth: `POST /api/v1/auth/login`"
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    register_middleware(app)

    if settings.PROMETHEUS_ENABLED:
        Instrumentator(
            should_group_status_codes=True,
            should_ignore_untemplated=True,
            excluded_handlers=["/health", "/metrics", "/", "/sw.js", "/manifest.json"],
        ).instrument(app).expose(app, include_in_schema=False)

    # Routers — CS agent endpoints come first in docs
    app.include_router(auth.router,          prefix="/api/v1")
    app.include_router(agents_router.router, prefix="/api/v1")  # primary
    app.include_router(chatbot.router,       prefix="/api/v1")
    app.include_router(endpoints.router,     prefix="/api/v1")
    app.include_router(frontend_router.router)  # must be last

    if (FRONTEND_DIR / "icons").exists():
        app.mount("/icons", StaticFiles(directory=str(FRONTEND_DIR / "icons")), name="icons")

    return app


app = create_app()


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version,
                         description=app.description, routes=app.routes)
    schema.setdefault("components", {})
    schema["components"]["securitySchemes"] = {
        "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    }
    schema["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
