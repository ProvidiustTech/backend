# Providius — Setup & Development Guide

> Production-grade AI Agents platform for Customer Service + Social Media automation  
> Integrated with FastAPI backend + Next.js frontend

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Node.js 18+ (for local frontend development)
- Python 3.12+ (for local backend development)
- Git

### 1. Clone & Configure

```bash
cd /Users/iboro/Desktop/Providius
# Configuration files are already set up:
# - .env              (backend configuration)
# - .env.example      (template for .env)
# - providius-dashboard/.env.local  (frontend configuration)
```

### 2. Start the Full Stack (Recommended)

```bash
# Start all services with Docker
make up

# Verify services are running:
# - API:         http://localhost:8000
# - Docs:        http://localhost:8000/docs
# - Frontend:    http://localhost:3000 (if running separately)
# - Prometheus:  http://localhost:9090
# - Grafana:     http://localhost:3000 (admin/integrateai_grafana)
```

### 3. Frontend-Backend Integration

The frontend is configured to communicate with the backend via the API client at `lib/api.ts`.

#### Running Frontend Separately (Development)

```bash
cd providius-dashboard
npm install
npm run dev

# Frontend will run at http://localhost:3000
# API calls will proxy to http://localhost:8000
```

---

## Project Structure

```
/Users/iboro/Desktop/Providius/
├── app/                           # FastAPI backend
│   ├── main.py                   # FastAPI app entry
│   ├── api/v1/                   # API endpoints
│   │   ├── agents.py            # Customer Service + Social Media agents
│   │   ├── chat.py              # Generic RAG chat
│   │   ├── auth.py              # JWT authentication
│   │   └── ...
│   ├── core/
│   │   ├── config.py            # Configuration (from .env)
│   │   ├── logging.py           # Structured logging
│   │   ├── langgraph/
│   │   │   ├── graph.py         # LangGraph pipeline
│   │   │   └── tools.py         # Agent tools
│   │   └── prompts/
│   │       └── system.md        # LLM system prompt
│   ├── models/                   # SQLAlchemy ORM models
│   ├── schemas/                  # Pydantic schemas
│   ├── services/                 # Business logic
│   │   ├── llm.py              # LLM provider management
│   │   ├── vector_store.py     # pgvector integration
│   │   ├── database.py         # Database utilities
│   │   └── ...
│   └── utils/                   # Utilities
│
├── providius-dashboard/          # Next.js frontend
│   ├── lib/
│   │   ├── api.ts              # API client for backend
│   │   └── conversations.ts    # Conversation state management
│   ├── src/app/
│   │   ├── page.tsx            # Landing page
│   │   └── dashboard/          # Dashboard pages
│   ├── public/                 # Static assets
│   ├── next.config.js          # Next.js configuration
│   └── package.json
│
├── docker-compose.yml          # Service orchestration
├── Dockerfile                  # Backend container
├── Makefile                    # Common commands
├── .env                        # Backend configuration
├── .env.example               # Configuration template
└── pyproject.toml             # Python dependencies
```

---

## Environment Variables

### Backend (.env)

**Required for Production:**
- `SECRET_KEY` — Min 32 characters
- `LLM_PROVIDER` — One of: `openai`, `anthropic`, `groq`, `ollama`
- API keys for selected LLM provider

**Database:**
```
DATABASE_URL=postgresql+asyncpg://integrateai:integrateai_secret@postgres:5432/integrateai_db
POSTGRES_HOST=postgres  # Use 'postgres' in Docker, 'localhost' locally
```

**LLM Selection:**
```
# Use local Ollama (free, no key needed)
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2

# Or use OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

### Frontend (.env.local)

```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_ENVIRONMENT=development
```

---

## Common Tasks

### Start Backend Only (Local)

```bash
# Install dependencies
make install

# Run migrations
make migrate

# Start dev server
make dev
# Runs on http://localhost:8000
```

### Start Frontend Only

```bash
cd providius-dashboard
npm install
npm run dev
# Runs on http://localhost:3000
```

### View Logs

```bash
# All services
make logs

# Specific service
docker compose logs app -f
docker compose logs postgres -f
```

### Run Tests

```bash
# Backend tests
make test

# With coverage
make eval
```

### Database Migrations

```bash
# Create new migration
make migrate

# Reset database (DEV ONLY!)
alembic downgrade base
alembic upgrade head
```

### Clean Up

```bash
# Stop all services
make down

# Remove all volumes (WARNING: deletes data)
docker compose down -v

# Clean Python cache
make clean
```

---

## API Endpoints

### Authentication

```bash
# Login
POST /api/v1/auth/login
{
  "email": "user@example.com",
  "password": "password123"
}

# Returns: { access_token, token_type, user }
```

### Chat (RAG)

```bash
# Send message
POST /api/v1/chat/message
Authorization: Bearer {token}
{
  "message": "What is your return policy?",
  "collection_id": "uuid"
}

# Stream message (SSE)
POST /api/v1/chat/stream
Authorization: Bearer {token}
{
  "message": "...",
  "collection_id": "uuid"
}
```

### Customer Service Agent

```bash
# Register company
POST /api/v1/agents/cs/register
{
  "company_name": "Example Corp",
  "company_url": "https://example.com"
}

# Chat with CS agent
POST /api/v1/agents/cs/chat
{
  "company_id": "uuid",
  "message": "How do I reset my password?"
}
```

See full docs: http://localhost:8000/docs

---

## Performance & Resource Management

### Memory Limits (Docker)

Resource limits are configured in `docker-compose.yml` to prevent machine overload:

- **PostgreSQL**: 512MB hard limit
- **FastAPI**: 1GB hard limit  
- **Prometheus**: 256MB hard limit
- **Grafana**: 256MB hard limit

To adjust, edit docker-compose.yml and restart:
```bash
docker compose down
docker compose up --build
```

### Optimization Tips

1. **Reduce chunk size** if indexing is slow (default: 512 tokens)
2. **Use Ollama locally** — no API costs, instant inference
3. **Disable Prometheus** in development if not needed:
   ```
   PROMETHEUS_ENABLED=false
   ```

4. **Frontend build optimization**:
   - `next.config.js` uses SWC for faster builds
   - Source maps disabled in production
   - Standalone builds are ~30MB (vs 200MB default)

---

## Monitoring

### Grafana Dashboard
- URL: http://localhost:3000
- Login: `admin` / `integrateai_grafana`
- Shows: API latency, error rates, token usage, hallucination scores

### Prometheus Metrics
- URL: http://localhost:9090
- Scrapes metrics from the FastAPI app every 15 seconds
- Key metrics: request_count, response_time, llm_tokens_total

### API Health Check
```bash
curl http://localhost:8000/health
# Returns: { "status": "ok" }
```

---

## Frontend API Integration

### Using the API Client

```typescript
// lib/api.ts provides pre-configured client
import { api, authApi, chatApi } from '@/lib/api';

// Authentication
const auth = await authApi.login({
  email: 'user@example.com',
  password: 'password123',
});

// Send a message
const response = await chatApi.sendMessage({
  message: 'Hello!',
  collection_id: 'collection-uuid',
});

// Stream a message
for await (const chunk of chatApi.streamMessage({...})) {
  console.log(chunk.token); // Token by token
}
```

### Conversation Management

```typescript
import { conversationStorage, createConversation, addMessage } from '@/lib/conversations';

// Create new conversation
let conv = createConversation('My Chat', 'collection-id');

// Add user message
conv = addMessage(conv, 'user', 'What is X?');

// Save to local storage
conversationStorage.save(conv);

// Load all conversations
const all = conversationStorage.getAll();
```

---

## Troubleshooting

### Docker Issues

```bash
# Containers not starting?
docker compose logs app

# Port conflicts?
sudo lsof -i :8000  # Find process on port 8000
kill -9 <PID>

# Volume permission issues?
docker compose down -v  # Remove volumes
docker compose up --build
```

### Backend Won't Start

```bash
# Check database connection
docker compose logs postgres

# Force migration
docker compose exec app alembic upgrade head

# Rebuild image
docker compose up --build -d
```

### Frontend Won't Connect to Backend

```bash
# Check API URL configuration
cat providius-dashboard/.env.local

# Verify backend is running
curl http://localhost:8000/health

# Check for CORS issues in browser console
# Browser -> DevTools -> Console
```

### Out of Memory

1. Check resource limits: `docker stats`
2. Reduce `CHUNK_SIZE` in .env (default 512)
3. Lower `TOP_K_RETRIEVE` (default 10)
4. Stop unused services:
   ```bash
   docker compose down
   docker compose up -d app postgres  # Only app + database
   ```

---

## Next Steps

1. **Upload Documents** → `POST /api/v1/documents/upload`
2. **Train Model** → Tuning prompts in `app/core/prompts/system.md`
3. **Monitor Metrics** → Check Grafana dashboard
4. **Deploy** → Choose: Railway, Heroku, AWS, DigitalOcean

---

## Support

For issues or questions:
1. Check logs: `make logs`
2. Review API docs: http://localhost:8000/docs
3. Check Grafana for metrics: http://localhost:3000

---

*Last Updated: April 22, 2026 — Providius Team*
