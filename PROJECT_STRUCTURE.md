# PROJECT_STRUCTURE.md

# Providius Project Structure & Guide

> Complete guide to Providius project files, directories, and their purposes

---

## Root Level

```
/Users/iboro/Desktop/Providius/
├── .env                           # Development environment variables (DO NOT COMMIT)
├── .env.example                   # Template for .env (commit this)
├── .gitignore                     # Git ignore rules
├── Dockerfile                     # Multi-stage Docker build
├── Makefile                       # Project commands (make help)
├── README.md                      # Project overview
├── SETUP.md                       # Development setup guide
├── DEPLOYMENT.md                  # Production deployment guide
├── AGENTS.md                      # IntegrateAI Blueprint documentation
├── PROJECT_STRUCTURE.md           # This file
├── pyproject.toml                 # Python project metadata & dependencies
├── alembic.ini                    # Database migration configuration
│
├── init-dev.sh                    # Quick setup script
├── verify-setup.sh                # Verification script
│
├── app/                           # FastAPI backend (Python)
├── providius-dashboard/           # Next.js frontend (TypeScript/React)
├── migrations/                    # Database migrations
├── evals/                         # Test files
├── scripts/                       # Database scripts
├── prometheus/                    # Prometheus configuration
├── grafana/                       # Grafana dashboards
│
└── docker-compose.yml             # Service orchestration
```

---

## Backend: `app/`

### Core Application Files

**`app/main.py`** — FastAPI application entry point
- Configures the FastAPI app
- Registers all route routers
- Sets up middleware, CORS, auth
- Defines startup/shutdown handlers
- Exposes `/docs` (Swagger UI)

**`app/core/config.py`** — Centralized configuration
- Pydantic settings from `.env`
- All environment variables defined here
- Validation logic for production deployment
- Safe defaults for development

**`app/core/logging.py`** — Structured JSON logging
- Configures log format (json/console)
- Uses `structlog` for structured logs
- Queryable in production via CloudWatch/Datadog

**`app/core/middleware.py`** — Request/response middleware
- CORS configuration
- Request logging
- Error handling
- Rate limiting setup

---

### API Routes: `app/api/v1/`

**`auth.py`** — Authentication endpoints
- `POST /api/v1/auth/login` — JWT token generation
- `POST /api/v1/auth/register` — User registration
- `GET /api/v1/auth/me` — Current user info
- `POST /api/v1/auth/refresh` — Refresh token

**`chatbot.py`** — Generic RAG chat endpoint
- `POST /api/v1/chat/message` — Send message (JSON response)
- `POST /api/v1/chat/stream` — Send message (SSE stream)
- Integrates with LangGraph RAG pipeline
- Handles hallucination detection

**`agents.py`** — AI Agents endpoints
- **Customer Service Agent** (PRIMARY)
  - `POST /api/v1/agents/cs/register` — Register company
  - `POST /api/v1/agents/cs/chat` — CS-specific chat
  - `POST /api/v1/agents/cs/chat/stream` — SSE stream
  - `GET /api/v1/agents/cs/sessions/{id}` — Get conversation history
  
- **Social Media Agent** (SECONDARY)
  - `POST /api/v1/agents/social/compose` — Generate post
  - `POST /api/v1/agents/social/trends` — Get trends
  - `POST /api/v1/agents/social/refine` — Refine draft

**`endpoints.py`** — Collection & Document management
- `POST /api/v1/collections` — Create collection
- `POST /api/v1/documents/upload` — Upload & index document
- `GET /api/v1/health` — Health check

**`frontend.py`** — Frontend serving
- Serves Next.js static assets (in production)
- Handles PWA manifest
- Health check endpoint for load balancers

---

### Data Models: `app/models/`

SQLAlchemy ORM models that map to PostgreSQL tables:

**`user.py`** — User account
```python
class User:
    id: UUID
    email: str (unique)
    hashed_password: str
    full_name: str
    created_at: datetime
```

**`collection.py`** — Document collection
```python
class Collection:
    id: UUID
    name: str
    user_id: UUID (FK → User)
    created_at: datetime
    metadata: dict
```

**`document.py`** — Documents in collection
```python
class Document:
    id: UUID
    collection_id: UUID (FK → Collection)
    name: str
    content: str
    metadata: dict
    indexed_at: datetime

class DocumentChunk:
    id: UUID
    document_id: UUID (FK → Document)
    text: str
    embedding: Vector[1536]  # pgvector embedding
    metadata: dict
    seq: int
```

**`cs.py`** — Customer Service specific models
```python
class CSSession:
    id: UUID
    company_id: UUID
    user_id: UUID
    messages: list[CSMessage]
    created_at: datetime

class CSMessage:
    id: UUID
    session_id: UUID
    role: "user" | "assistant"
    content: str
    sources: list[str]
    timestamp: datetime
```

**`social.py`** — Social Media agent models
```python
class SocialProfile:
    id: UUID
    user_id: UUID
    platform: "twitter" | "linkedin" | "instagram"
    handle: str
    
class SocialPost:
    id: UUID
    profile_id: UUID
    content: str
    scheduled_at: datetime
    posted_at: datetime
```

---

### Request/Response Schemas: `app/schemas/`

Pydantic models for API validation:

**`chat.py`** — Chat request/response
```python
class ChatRequest:
    query: str
    collection_id: UUID
    conversation_id: UUID | None
    metadata_filter: dict | None

class ChatResponse:
    id: str
    response: str
    sources: list[SourceReference]

class SourceReference:
    doc_id: str
    doc_title: str
    relevance_score: float
    excerpt: str
```

**`auth.py`** — Auth request/response
```python
class LoginRequest:
    email: str
    password: str

class AuthResponse:
    access_token: str
    token_type: str
    user: User
```

**`cs.py`** — CS Agent schemas
**`social.py`** — Social Media Agent schemas
**`document.py`** — Collection/Document schemas

---

### Business Logic: `app/services/`

**`llm.py`** — LLM provider abstraction
- Supports: OpenAI, Anthropic, Groq, Ollama
- Instantiates correct LLM based on `LLM_PROVIDER`
- Handles API key rotation

**`vector_store.py`** — pgvector integration
- Hybrid search (dense + BM25)
- Chunk retrieval with metadata filtering
- Embedding generation & storage

**`database.py`** — Database utilities
- Connection pool management
- Health checks
- Migration utilities

**`case_history.py`** — CS case management
- Save resolved customer queries
- Retrieve case history
- Analytics on resolution rates

**`web_scraper.py`** — Company website scraping
- Fetch company knowledge base
- Parse HTML → document chunks
- Background job processing

---

### LangGraph Pipeline: `app/core/langgraph/`

**`graph.py`** — RAG pipeline orchestration
```
User Query
    ↓
[Node 1: Retrieve] — pgvector search + metadata filtering
    ↓
[Node 2: Rerank] — Cohere rerank top-5
    ↓
[Node 3: Generate] — LLM answers with citations
    ↓
[Node 4: Validate] — Hallucination detection
    ↓
Final Answer + Sources
```

**`tools.py`** — LLM tools/functions
- Web search tool
- SQL query tool
- Function calling definitions

**`prompts/system.md`** — LLM system prompt
- Defines agent behavior
- Tone and style guidelines
- Output formatting instructions

---

### Utilities: `app/utils/`

**`chunking.py`** — Document chunking
- Semantic chunking
- Token-based splitting
- Overlap handling

**`file_parser.py`** — Document parsing
- PDF, DOCX, TXT extraction
- Format detection
- Content cleaning

**`text.py`** — Text utilities
- Tokenization
- Embedding dimension mapping
- Text normalization

---

## Frontend: `providius-dashboard/`

### Configuration

**`next.config.js`** — Next.js configuration
- Standalone builds (30MB)
- SWC minification
- Image optimization
- CORS headers

**`tsconfig.json`** — TypeScript configuration
- Path aliases: `@/*` → `src/*`
- Strict mode enabled
- ES2017 target

**`tailwind.config.js`** — Tailwind CSS configuration
- Theme colors
- Component utilities
- Dark mode support

**`package.json`** — Dependencies
- Next.js 16
- React 18
- Tailwind CSS 3
- Recharts (charting)
- Zustand (state management)
- SWR (data fetching)

---

### Frontend API Integration: `lib/`

**`api.ts`** — HTTP client for backend
```typescript
// Authentication
authApi.login({email, password}) → AuthResponse

// Chat messaging
chatApi.sendMessage({message, collection_id}) → ChatResponse
chatApi.streamMessage({...}) → AsyncGenerator<StreamChunk>

// SSE streaming helper
api.streamSSE<T>(path, init) → AsyncGenerator<T>
```

**`conversations.ts`** — Conversation state management
```typescript
interface Conversation {
  id: string
  title: string
  messages: Message[]
  created_at: Date
}

// Local storage persistence
conversationStorage.getAll() → Conversation[]
conversationStorage.save(conv)
conversationStorage.delete(id)

// Helpers
createConversation(title, collection_id)
addMessage(conv, role, content, sources)
```

---

### Application: `src/app/`

**`layout.tsx`** — Root layout
- Provider setup (Zustand, Theme)
- Global styles
- Metadata

**`page.tsx`** — Landing/Login page
- Sign-up form
- Feature showcase
- Stats carousel

**`dashboard/`** — Dashboard pages
- `page.tsx` — Dashboard overview
- `conversations/` — Conversation list & detail
- `knowledge-base/` — Document management
- `analytics/` — Usage analytics
- `settings/` — User settings
- `channels/` — Agent channel configuration

---

### Components: `src/components/`

**Shared UI Components**
- `Sidebar.tsx` — Navigation sidebar
- `MobileNav.tsx` — Mobile navigation
- `Stepper.tsx` — Multi-step forms
- `ThemeProvider.tsx` — Dark mode toggle

**Icons** — Reusable SVG icons
- `DashboardIcon.tsx`
- `ConversationsIcon.tsx`
- `KnowledgeBaseIcon.tsx`
- `SettingsIcon.tsx`
- etc.

---

### Styling

**`src/app/globals.css`** — Global styles
- Tailwind directives
- CSS variables for theming
- Reset styles

**Tailwind Classes** — Utility-first CSS
- Components built with Tailwind
- Dark mode support: `dark:bg-gray-900`
- Responsive: `md:`, `lg:`, `xl:` prefixes

---

## Database: `migrations/`

**`env.py`** — Alembic configuration
- Database connection
- Target metadata setup
- Migration runner

**`versions/`** — Migration scripts
- Version up/down scripts
- Sequential numbering
- Tracks schema changes

Run migrations:
```bash
alembic upgrade head      # Apply all migrations
alembic downgrade base    # Revert to base
alembic current           # Show current version
```

---

## Testing: `evals/`

**`conftest.py`** — Pytest fixtures
- Database setup/teardown
- Test client setup
- Mock data generators

**`test_rag.py`** — RAG pipeline tests
- Retrieval accuracy
- Reranking effectiveness
- Hallucination detection

**`test_cs_agent.py`** — Customer Service agent tests
**`test_social_agent.py`** — Social Media agent tests

Run tests:
```bash
make test          # All tests
make eval          # RAG evaluations only
make bench         # Latency benchmarks
```

---

## Monitoring: `prometheus/`, `grafana/`

**`prometheus/prometheus.yml`** — Prometheus scrape config
- Scrapes metrics from FastAPI app
- Retention: 30 days
- Interval: 15 seconds

**`grafana/dashboards/integrateai.json`** — Dashboard definition
- API latency (p50, p95, p99)
- Request rate
- Error rate by status
- LLM token usage
- Hallucination score
- Vector store latency

---

## Database Scripts: `scripts/`

**`init_db.sql`** — Database initialization
- Creates initial extensions (pgvector, uuid)
- Sets up schemas
- Creates users

---

## Configuration Files

**`.gitignore`** — Git ignore rules
- `.env` files (secrets)
- Node modules
- Python cache
- Build artifacts

**`alembic.ini`** — Migration config
**`pyproject.toml`** — Python project metadata
**`docker-compose.yml`** — Service orchestration

---

## Key Files Checklist

| File | Purpose | Status |
|------|---------|--------|
| `.env` | Development secrets | ✅ Created |
| `.env.example` | Configuration template | ✅ Created |
| `Dockerfile` | Backend containerization | ✅ Exists |
| `docker-compose.yml` | Service orchestration | ✅ Updated |
| `app/main.py` | FastAPI entry | ✅ Configured |
| `app/core/config.py` | Configuration | ✅ Configured |
| `providius-dashboard/lib/api.ts` | Frontend API client | ✅ Created |
| `providius-dashboard/lib/conversations.ts` | Conversation management | ✅ Created |
| `providius-dashboard/next.config.js` | Next.js optimization | ✅ Updated |
| `providius-dashboard/package.json` | Frontend dependencies | ✅ Updated |
| `Makefile` | Project commands | ✅ Updated |
| `SETUP.md` | Development guide | ✅ Created |
| `DEPLOYMENT.md` | Production guide | ✅ Created |

---

## Quick Commands

```bash
# Setup & Install
make init              # One-time project initialization
make install           # Install Python dependencies

# Development
make dev              # Start backend (hot reload)
make frontend         # Start frontend (Next.js)

# Docker
make up               # Start all services
make down             # Stop all services
make logs             # View logs

# Database
make migrate          # Run migrations
make migration MSG="description"  # Create new migration

# Testing
make test             # Run tests
make eval             # RAG evaluations
make lint             # Linting
make fmt              # Auto-format

# Cleanup
make clean            # Remove caches/artifacts
```

---

## File Size & Optimization

| Service | Size | Memory | CPU |
|---------|------|--------|-----|
| PostgreSQL | 100MB | 512MB | 1.5 |
| FastAPI | 200MB | 1GB | 2 |
| Next.js (built) | 30MB | 256MB | 1 |
| Prometheus | 50MB | 256MB | 1 |
| Grafana | 100MB | 256MB | 1 |
| **Total** | **480MB** | **2.25GB** | **6.5** |

Run only what you need:
- Backend only: `make backend`
- Without monitoring: `docker compose down && docker compose up -d app postgres`

---

## Integration Points

### Frontend ↔ Backend

1. **API Client** — `providius-dashboard/lib/api.ts`
   - All HTTP requests go through here
   - Handles JWT token management
   - Configurable base URL via `NEXT_PUBLIC_API_URL`

2. **Conversation Sync**
   - Frontend stores conversations in localStorage
   - Backend tracks in PostgreSQL
   - Sync on login/logout

3. **Authentication Flow**
   ```
   User Login (page.tsx)
     ↓
   api.login() calls /api/v1/auth/login
     ↓
   Stores JWT token in localStorage
     ↓
   All subsequent requests include Authorization header
   ```

4. **Chat Flow**
   ```
   User message (conversations/)
     ↓
   chatApi.streamMessage() → POST /api/v1/chat/stream
     ↓
   SSE stream: token by token
     ↓
   Display with sources (SourceReference)
     ↓
   Save to conversationStorage + optional backend
   ```

---

## Environment Variables Summary

### Backend (.env)

**Security**
- `SECRET_KEY` — Min 32 chars
- `JWT_SECRET_KEY` — JWT signing key
- `ENVIRONMENT` — development/production

**Database**
- `DATABASE_URL` — PostgreSQL connection
- `POSTGRES_*` — Individual credentials

**LLM**
- `LLM_PROVIDER` — openai/anthropic/groq/ollama
- `API keys` for selected provider

**Monitoring**
- `PROMETHEUS_ENABLED` — true/false
- `LOG_LEVEL` — DEBUG/INFO/WARNING/ERROR

### Frontend (.env.local)

- `NEXT_PUBLIC_API_URL` — Backend URL
- `NEXT_PUBLIC_ENVIRONMENT` — development/production

---

## Deployment Checklist

- [ ] Create `.env` from `.env.example`
- [ ] Set all API keys
- [ ] Update database connection string
- [ ] Configure LLM provider
- [ ] Run migrations: `make migrate`
- [ ] Build images: `docker build -t providius-api:latest .`
- [ ] Start services: `docker compose up -d`
- [ ] Check health: `curl http://localhost:8000/health`
- [ ] View logs: `docker compose logs app`
- [ ] Monitor metrics: `open http://localhost:3000` (Grafana)

---

*Last Generated: April 22, 2026 — Providius Team*
