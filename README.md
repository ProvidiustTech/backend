# Providius — AI Agents Platform

> Production-grade AI Agents platform for Customer Service + Social Media automation

**Status:** ✅ Ready for development & deployment

---

## What Is Providius?

Providius is a **full-stack RAG + Agentic AI platform** that enables businesses to:

- **Build AI Agents** for customer service, social media, and more
- **Integrate with company knowledge bases** (PDFs, websites, documents)
- **Deploy in 5-10 days** with a production-ready framework
- **Monitor performance** with built-in metrics & dashboards
- **Scale to thousands of users** with smart caching & optimization

### Two Main Agents

| Agent | Purpose | Primary Use |
|-------|---------|-------------|
| **Customer Service** | Answer customer questions grounded in company knowledge | Support teams, FAQs, help desk |
| **Social Media** | Generate trend-aware posts and manage scheduling | Marketing, social media teams |

---

## Quick Start

### 1. Clone & Initialize

```bash
cd /Users/iboro/Desktop/Providius

# One-time setup (installs everything)
bash init-dev.sh

# Or manually
make init
```

### 2. Start Services

```bash
# Terminal 1: Start backend + database
make up

# Terminal 2: Start frontend
cd providius-dashboard
npm run dev
```

### 3. Access the Platform

| Service | URL |
|---------|-----|
| **Frontend** | http://localhost:3000 |
| **API** | http://localhost:8000 |
| **API Docs** | http://localhost:8000/docs |
| **Grafana** | http://localhost:3000 (admin/integrateai_grafana) |

Done! 🎉

---

## Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | Next.js 16 + React 18 + Tailwind CSS |
| **Backend** | FastAPI + PostgreSQL + pgvector |
| **LLM** | OpenAI / Anthropic / Groq / Ollama |
| **Orchestration** | LangGraph + LangChain |
| **RAG** | LlamaIndex + Cohere Rerank |
| **Monitoring** | Prometheus + Grafana |
| **Container** | Docker + Docker Compose |

---

## Documentation

| Document | Purpose |
|----------|---------|
| **[FIX_SUMMARY.md](FIX_SUMMARY.md)** | What was fixed & how to start |
| **[SETUP.md](SETUP.md)** | Development setup & usage guide |
| **[DEPLOYMENT.md](DEPLOYMENT.md)** | Production deployment guide |
| **[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)** | Complete file guide |
| **[AGENTS.md](AGENTS.md)** | IntegrateAI Blueprint reference |

---

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Browser / Mobile App                       │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ↓
      ┌────────────────────────────────────┐
      │  Frontend (Next.js @ :3000)         │
      │  - Dashboard / Chat Interface      │
      │  - Conversation Management        │
      │  - Analytics / Settings            │
      └────────────────┬───────────────────┘
                       │ (HTTP REST / SSE)
                       ↓
      ┌────────────────────────────────────┐
      │  Backend API (FastAPI @ :8000)     │
      │  - /api/v1/auth - JWT auth        │
      │  - /api/v1/chat - RAG chat        │
      │  - /api/v1/agents - AI agents    │
      │  - /api/v1/documents - Knowledge │
      └────────────────┬───────────────────┘
                       │
          ┌────────────┼────────────┐
          ↓            ↓            ↓
      ┌────────┐  ┌──────────┐  ┌─────────┐
      │ Proms  │  │LangGraph │  │Database │
      │ OpenAI │  │ Tools    │  │Postgres │
      │ Groq   │  │ RAG      │  │pgvector │
      │ Ollama │  │ Pipeline │  │         │
      └────────┘  └──────────┘  └─────────┘
       
       ┌─────────────────────────────────┐
       │  Monitoring & Observability      │
       │  - Prometheus (metrics)          │
       │  - Grafana (dashboards)          │
       │  - Structured Logging            │
       └─────────────────────────────────┘
```

---

## Common Tasks

### 🚀 Development

```bash
make help              # See all commands
make dev              # Run backend (hot reload)
make frontend         # Run frontend (Next.js)
make up               # Start all services
make logs             # View logs
make health           # Check health
```

### 📦 Database

```bash
make migrate          # Run migrations
make migration MSG="Add feature"  # Create migration
make db-reset         # Reset database (DEV ONLY)
```

### ✅ Testing & Quality

```bash
make test             # Run tests
make eval             # RAG evaluations
make lint             # Check code
make fmt              # Auto-format
```

### 🧹 Cleanup

```bash
make clean            # Remove caches
make down             # Stop services
```

See all commands: `make help`

---

## Configuration

### Backend (.env)

Create from template:
```bash
cp .env.example .env
```

**Key Variables:**
- `LLM_PROVIDER` — Which AI model (openai/anthropic/groq/ollama)
- `DATABASE_URL` — PostgreSQL connection
- `OPENAI_API_KEY` — If using OpenAI
- See [.env.example](.env.example) for all 40+ variables

### Frontend (.env.local)

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_ENVIRONMENT=development
```

---

## API Examples

### Login

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "password123"
  }'
```

### Send Chat Message

```bash
curl -X POST http://localhost:8000/api/v1/chat/message \
  -H "Authorization: Bearer <TOKEN>" \
  -d '{
    "message": "What is your return policy?",
    "collection_id": "9e7c8f5a-1234-5678-90ab-cdef01234567"
  }'
```

### Stream Response (SSE)

```typescript
for await (const chunk of chatApi.streamMessage({
  message: 'Hello!',
  collection_id: 'collection-uuid',
})) {
  console.log(chunk.token);   // Token by token
  console.log(chunk.sources); // With citations
}
```

Full API docs: http://localhost:8000/docs

---

## Features

### 🤖 AI Agents

- **Customer Service Agent** — Answer questions from company knowledge base
- **Social Media Agent** — Generate trend-aware social media posts
- **Extensible** — Add custom agents via LangGraph

### 📚 Knowledge Management

- Upload documents (PDF, DOCX, TXT)
- Automatic chunking & embedding
- Hybrid search (dense + BM25)
- Metadata filtering

### 🔍 Search & Retrieval

- Vector similarity search (pgvector)
- Reranking with Cohere
- Multi-document citations
- Metadata filtering

### 🔐 Security

- JWT authentication
- Rate limiting per user
- CORS protection
- Audit logging

### 📊 Monitoring

- Real-time metrics (Prometheus)
- Interactive dashboards (Grafana)
- Structured JSON logging
- Hallucination detection

### 🚀 Production-Ready

- Docker containerization
- Resource limits (prevents overload)
- HealthChecks
- Database backups
- Auto-scaling ready
- Multiple LLM providers

---

## LLM Provider Options

Choose your LLM provider via `LLM_PROVIDER`:

| Provider | Cost | Speed | Quality | Setup | Best For |
|----------|------|-------|---------|-------|----------|
| **Ollama** | Free | Fast | Good | Local | Development, demos |
| **Groq** | $$ | ⚡ Fastest | Good | API key | Production (speed) |
| **OpenAI** | $$$ | Slow | Best | API key | Production (quality) |
| **Anthropic** | $$ | Medium | Best | API key | Production (safety) |

**Default:** `ollama` (free, local, no API key needed)

---

## Resource Usage

When running `make up`:

| Service | Memory | CPU | Duration |
|---------|--------|-----|----------|
| PostgreSQL | 512MB | 1.5 | Persistent |
| FastAPI | 1GB | 2 | Persistent |
| Prometheus | 256MB | 1 | Persistent |
| Grafana | 256MB | 1 | Persistent |
| **Total** | **2.25GB** | **5.5** | Per-container |

**Right-size for your machine:**
- Too slow? Run backend only: `make backend`
- Too much memory? Skip monitoring: `docker compose down && docker compose up -d app postgres`

---

## Deployment

Ready for production?

1. See [DEPLOYMENT.md](DEPLOYMENT.md) for:
   - Cloud deployment (AWS, Azure, Railway, Heroku)
   - Production configuration
   - Security hardening
   - Performance tuning
   - Cost optimization

2. Quick deploy to Railway:
   ```bash
   npm i -g @railway/cli
   railway login
   railway init
   railway up
   ```

---

## Troubleshooting

### Services won't start?
```bash
make logs  # Check what went wrong
make health  # Quick health check
```

### API connection error?
```bash
# Check backend is running
curl http://localhost:8000/health

# Check frontend env
cat providius-dashboard/.env.local
```

### Out of memory?
```bash
# Use smaller config
docker compose down
docker compose up -d app postgres  # Database + API only
```

### Database issues?
```bash
# Reset database (DEV ONLY)
make db-reset
```

More troubleshooting: See [SETUP.md](SETUP.md) → Troubleshooting

---

## Next Steps

1. **[Read FIX_SUMMARY.md](FIX_SUMMARY.md)** — Understand what was fixed
2. **[Run `make init`](Makefile)** — One-time setup
3. **[Open http://localhost:3000](http://localhost:3000)** — Start using the platform
4. **[Upload documents](SETUP.md#uploading-documents)** — Train your knowledge base
5. **[Deploy to production](DEPLOYMENT.md)** — Ship to users

---

## Support

- 📖 **Docs**: [FIX_SUMMARY.md](FIX_SUMMARY.md), [SETUP.md](SETUP.md), [DEPLOYMENT.md](DEPLOYMENT.md)
- 🐛 **Issues**: Check logs with `make logs`
- 📧 **Email**: support@providius.io
- 🔗 **API Docs**: http://localhost:8000/docs

---

## License

MIT — See LICENSE file

---

## Team

Built with ❤️ by the Providius team

- **Created:** April 2026
- **Status:** Production-ready
- **Maintenance:** Active

---

**Ready to get started?**

```bash
make init    # Setup everything
npm run dev  # Start developing!
```

Enjoy building with Providius! 🚀


# 2. Login → get token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -F "username=dev@agency.io" -F "password=secret1234" | jq -r .access_token)

# 3. Create a collection
COLLECTION_ID=$(curl -s -X POST http://localhost:8000/api/v1/collections \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Finance Docs","vertical":"finance"}' | jq -r .id)

# 4. Upload a document
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@report.txt" \
  -F "collection_id=$COLLECTION_ID" \
  -F "title=Q3 Report"

# 5. Chat (streaming)
curl -N -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"What was Q3 revenue?\",\"collection_id\":\"$COLLECTION_ID\",\"stream\":true}"
```

---

## Development

```bash
make install    # Install deps with uv
make dev        # Hot-reload dev server
make test       # Run all tests
make eval       # Run RAG accuracy evals
make lint       # Ruff linter
make fmt        # Auto-format
```

---

## Architecture

```
┌─────────────┐    JWT     ┌─────────────────────────────────────────┐
│   Client    │──────────▶│            FastAPI App                   │
│ (React/API) │           │                                          │
└─────────────┘    SSE    │  /api/v1/chat ──▶ LangGraph RAG Pipeline │
        ▲──────────────────│                                          │
                          │  retrieve ─▶ rerank ─▶ generate ─▶ validate │
                          └──────────────────────────────────────────┘
                                          │
                          ┌───────────────┼───────────────┐
                          │               │               │
                    pgvector DB      OpenAI/Cohere    Prometheus
                    (chunks +        (LLM + rerank)   + Grafana
                     embeddings)
```

---

See [AGENTS.md](./AGENTS.md) for the full client delivery playbook.
