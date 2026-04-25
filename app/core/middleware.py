"""
app/core/middleware.py
======================
All FastAPI middleware in one place:
  1. CORS
  2. Request-ID injection (for distributed tracing / log correlation)
  3. Structured access logging
  4. Global error handler

Rate limiting is applied per-route via SlowAPI decorators in api/v1/endpoints.py
"""

import time
import uuid
from collections.abc import Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Generates a unique request_id for every inbound request and:
      - Attaches it to structlog context (visible in all log lines for this req)
      - Returns it in the X-Request-ID response header for client-side tracing
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Bind to structlog context so all log lines in this request carry it
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        response.headers["X-Request-ID"] = request_id

        log.info(
            "request completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all handler that returns a clean JSON error instead of a 500 traceback.
    Logs the full exception with the request_id for easy debugging.
    """
    log.exception("unhandled exception", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred. Please try again.",
            "request_id": structlog.contextvars.get_contextvars().get("request_id"),
        },
    )


def register_middleware(app: FastAPI) -> None:
    """
    Called once from main.py to attach all middleware to the FastAPI app.
    Order matters: middleware is applied bottom-up (last added = outermost).
    """

    # 1. CORS (outermost — must be first in stack so preflight works)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # 2. Request ID + access log
    app.add_middleware(RequestIDMiddleware)

    # 3. Global exception handler
    app.add_exception_handler(Exception, global_exception_handler)
