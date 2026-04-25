# =============================================================================
# IntegrateAI Blueprint — Multi-stage Dockerfile
# Uses uv for fast, deterministic dependency installation
# =============================================================================

# ── Base: Python 3.12 slim ───────────────────────────────────────────────────
FROM python:3.12-slim AS base

# Install system deps needed by psycopg2, cryptography, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (ultra-fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests first (cache layer)
COPY pyproject.toml ./
COPY README.md ./

# ── Builder: install deps ────────────────────────────────────────────────────
FROM base AS builder

# Create virtual env and install all dependencies via uv
RUN uv venv /app/.venv && \
    uv pip install --python /app/.venv/bin/python -e ".[dev]"

# ── Development target ───────────────────────────────────────────────────────
FROM base AS development

COPY --from=builder /app/.venv /app/.venv
COPY . .

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── Production target ────────────────────────────────────────────────────────
FROM base AS production

# Non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

COPY --from=builder /app/.venv /app/.venv
COPY . .

RUN chown -R appuser:appuser /app
USER appuser

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Gunicorn with uvicorn workers for production
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--loop", "uvloop", \
     "--http", "httptools"]
