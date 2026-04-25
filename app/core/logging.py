"""
app/core/logging.py
===================
Structured JSON logging using structlog.
Every log line is a machine-parseable JSON object with:
  - timestamp (ISO 8601)
  - level
  - event (message)
  - service, environment, version
  - request_id (injected by middleware)
  - any extra kwargs passed at call site

Usage:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.info("Document indexed", collection_id=cid, chunks=42, latency_ms=120)
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.config import settings


def _add_service_context(
    logger: Any, method_name: str, event_dict: EventDict
) -> EventDict:
    """Inject global service metadata into every log record."""
    event_dict["service"] = settings.APP_NAME
    event_dict["version"] = settings.APP_VERSION
    event_dict["environment"] = settings.ENVIRONMENT
    return event_dict


def _drop_color_message_key(
    logger: Any, method_name: str, event_dict: EventDict
) -> EventDict:
    """Remove uvicorn's color_message key to keep JSON clean."""
    event_dict.pop("color_message", None)
    return event_dict


def setup_logging() -> None:
    """
    Call once at application startup (inside lifespan or main.py).
    Configures both structlog and the stdlib logging bridge.
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # ── Shared processors ─────────────────────────────────────────────────────
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service_context,
        _drop_color_message_key,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.LOG_FORMAT == "json":
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        # Pretty console output for local development
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Quiet noisy libraries
    for noisy in ("uvicorn.access", "httpcore", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Factory used throughout the codebase.

    Example:
        log = get_logger(__name__)
        log.info("chat request", user_id=uid, query_len=len(q))
    """
    return structlog.get_logger(name)
