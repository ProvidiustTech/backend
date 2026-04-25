# =============================================================================
# Providius — Makefile
# Usage: make <target>
# =============================================================================

.PHONY: help init install dev up down logs test eval lint fmt migrate clean frontend backend

# Default target
help:
	@echo ""
	@echo "  Providius — Available Commands"
	@echo "  ──────────────────────────────────────────────"
	@echo ""
	@echo "  Setup & Initialization"
	@echo "  ├─ make init         Initialize project (setup env, install deps)"
	@echo "  ├─ make install      Install Python dependencies only"
	@echo "  └─ make clean        Clean caches and build artifacts"
	@echo ""
	@echo "  Local Development"
	@echo "  ├─ make dev          Start backend only (hot reload)"
	@echo "  ├─ make frontend     Start frontend only (Next.js)"
	@echo "  └─ make backend      Start backend as Docker container"
	@echo ""
	@echo "  Docker & Orchestration"
	@echo "  ├─ make up           Start all services (Compose)"
	@echo "  ├─ make down         Stop all services"
	@echo "  ├─ make logs         Tail service logs"
	@echo "  └─ make restart      Restart all services"
	@echo ""
	@echo "  Testing & Quality"
	@echo "  ├─ make test         Run unit tests"
	@echo "  ├─ make eval         Run RAG evaluations"
	@echo "  ├─ make lint         Lint code (ruff)"
	@echo "  ├─ make fmt          Auto-format code"
	@echo "  └─ make typecheck    Type checking (mypy)"
	@echo ""
	@echo "  Database"
	@echo "  ├─ make migrate      Run migrations"
	@echo "  ├─ make migration    Create new migration (MSG='description')"
	@echo "  └─ make db-reset     Reset database (DEV ONLY)"
	@echo ""

# ── Setup & Installation ────────────────────────────────────────────────────

init:
	@echo "Initializing Providius..."
	@chmod +x init-dev.sh
	@bash init-dev.sh

install:
	uv sync --all-extras
	@echo "✓ Python dependencies installed"

# ── Local Development ───────────────────────────────────────────────────────

dev:
	cp -n .env.example .env 2>/dev/null || true
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --log-level info

frontend:
	cd providius-dashboard && npm install && npm run dev

backend:
	cp -n .env.example .env 2>/dev/null || true
	docker compose up -d app postgres
	docker compose logs app -f

# ── Docker Orchestration ────────────────────────────────────────────────────

up:
	cp -n .env.example .env 2>/dev/null || true
	docker compose up --build -d
	@echo ""
	@echo "✓ Providius running!"
	@echo "  API:        http://localhost:8000"
	@echo "  Docs:       http://localhost:8000/docs"
	@echo "  Prometheus: http://localhost:9090"
	@echo "  Grafana:    http://localhost:3000 (admin/integrateai_grafana)"
	@echo ""

down:
	docker compose down

logs:
	docker compose logs app -f --tail=100

restart:
	docker compose restart

ps:
	docker compose ps

health:
	@echo "Checking service health..."
	@curl -f http://localhost:8000/health && echo "✓ Backend OK" || echo "✗ Backend DOWN"
	@docker compose logs postgres | tail -5

# ── Testing ────────────────────────────────────────────────────────────────

test:
	pytest evals/ -v --cov=app --cov-report=term-missing

eval:
	pytest evals/ -v -k "not Latency" --tb=short

bench:
	pytest evals/ -v -k "Latency" -s

# ── Code Quality ───────────────────────────────────────────────────────────

lint:
	ruff check app/ evals/

fmt:
	ruff format app/ evals/
	ruff check --fix app/ evals/

typecheck:
	mypy app/ --ignore-missing-imports

# ── Database ───────────────────────────────────────────────────────────────

migrate:
	alembic upgrade head

migration:
	alembic revision --autogenerate -m "$(MSG)"

db-reset:
	@echo "WARNING: This will delete all data!"
	@read -p "Are you sure? (yes/no) " -n 3 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy][Ee][Ss]$$ ]]; then \
		docker compose down -v; \
		docker compose up -d postgres; \
		sleep 5; \
		alembic upgrade head; \
		echo "✓ Database reset"; \
	else \
		echo "Cancelled"; \
	fi

# ── Cleanup ────────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .next -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
	rm -rf htmlcov .coverage dist build
	@echo "✓ Cleaned"
