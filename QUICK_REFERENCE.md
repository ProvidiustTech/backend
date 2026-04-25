# QUICK_REFERENCE.md

# Providius — Quick Reference Guide

> One-page cheat sheet for common tasks

---

## Starting Up

```bash
# Automated setup (one-time)
bash init-dev.sh

# Manual setup
make init
make up
cd providius-dashboard && npm run dev
```

**Check it worked:**
- Frontend: http://localhost:3000
- Backend: http://localhost:8000/docs
- Metrics: http://localhost:9090

---

## Common Make Commands

```bash
# Start/Stop
make up                      # Start all services
make down                    # Stop all services
make logs                    # View logs
make health                  # Quick health check

# Development
make dev                     # Backend only (hot reload)
make frontend                # Frontend only (Next.js)
make backend                 # Backend as Docker

# Database
make migrate                 # Apply migrations
make migration MSG="desc"    # Create migration
make db-reset                # Reset database

# Testing
make test                    # Run tests
make eval                    # RAG evaluations
make lint                    # Check code style
make fmt                     # Auto-format

# Cleanup
make clean                   # Remove build artifacts
```

---

## Frontend API Usage

```typescript
// Import API client
import { api, authApi, chatApi } from '@/lib/api';
import { conversationStorage, createConversation, addMessage } from '@/lib/conversations';

// ── Login ──────────────────────────────────────────────────────────────────
const auth = await authApi.login({
  email: 'user@example.com',
  password: 'password123',
});
// Token automatically stored in localStorage

// ── Send Message (JSON) ────────────────────────────────────────────────────
const response = await chatApi.sendMessage({
  message: 'What is your return policy?',
  collection_id: 'uuid...',
});

console.log(response.response);  // AI's answer
console.log(response.sources);   // Cited documents

// ── Stream Message (Real-time) ─────────────────────────────────────────────
for await (const chunk of chatApi.streamMessage({
  message: 'Hello!',
  collection_id: 'uuid...',
})) {
  console.log(chunk.token);    // Token by token
  console.log(chunk.sources);  // With sources
  console.log(chunk.done);     // When finished
}

// ── Manage Conversations ───────────────────────────────────────────────────
// Create
let conv = createConversation('My Chat', 'collection-uuid');

// Add messages
conv = addMessage(conv, 'user', 'Question here?');
conv = addMessage(conv, 'assistant', 'Answer here', [{...sources}]);

// Save to localStorage
conversationStorage.save(conv);

// Load all
const all = conversationStorage.getAll();

// Delete
conversationStorage.delete(conv.id);
```

---

## Backend API Endpoints

### Authentication

```bash
# Login
POST /api/v1/auth/login
{
  "email": "user@example.com",
  "password": "password123"
}

# Register
POST /api/v1/auth/register
{
  "email": "user@example.com",
  "password": "password123",
  "full_name": "User Name"
}

# Check auth
GET /api/v1/auth/me
(requires Authorization header)

# Refresh token
POST /api/v1/auth/refresh
```

### Chat (RAG)

```bash
# Send message (JSON)
POST /api/v1/chat/message
{
  "message": "What is your policy?",
  "collection_id": "uuid..."
}

# Stream message (SSE)
POST /api/v1/chat/stream
(same payload, returns Server-Sent Events)
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
  "company_id": "uuid...",
  "message": "How do I reset my password?"
}

# List sessions
GET /api/v1/agents/cs/sessions

# Get session detail
GET /api/v1/agents/cs/sessions/{id}
```

### Document Management

```bash
# Create collection
POST /api/v1/collections
{
  "name": "Finance Docs",
  "metadata": {"type": "public"}
}

# List collections
GET /api/v1/collections

# Upload document
POST /api/v1/documents/upload
(multipart/form-data with file)

# List documents
GET /api/v1/documents

# Delete document
DELETE /api/v1/documents/{id}
```

### Health & Monitoring

```bash
# Health check
GET /api/v1/health

# Metrics (Prometheus)
GET /metrics

# Full API docs
GET /docs
```

---

## Environment Variables

### Required

```bash
# .env
ENVIRONMENT=development
SECRET_KEY=<32+ character string>
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db

# Choose ONE LLM provider:
LLM_PROVIDER=ollama
# OR
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
# OR
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
# OR
LLM_PROVIDER=groq
GROQ_API_KEY=...
```

### Optional (defaults fine for dev)

```bash
DEBUG=true
LOG_LEVEL=INFO
RATE_LIMIT_PER_MINUTE=60
CHUNK_SIZE=512
TOP_K_RETRIEVE=10
SIMILARITY_THRESHOLD=0.7
PROMETHEUS_ENABLED=true
```

### Frontend

```bash
# providius-dashboard/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_ENVIRONMENT=development
```

---

## Database Schema (Quick)

**Main Tables:**

```sql
-- Users
users (id, email, password, full_name, created_at)

-- Documents & Collections
collections (id, user_id, name, metadata)
documents (id, collection_id, name, content, metadata)
document_chunks (id, document_id, text, embedding[1536], metadata)

-- Chat
cs_sessions (id, company_id, created_at)
cs_messages (id, session_id, role, content, sources)

-- Metadata
users (id, email, created_at)
```

---

## Debugging

### Check Logs

```bash
# All services
make logs

# Specific service
docker compose logs app -f

# Follow + tail
docker compose logs -f --tail=50 app
```

### Check Health

```bash
# API
curl http://localhost:8000/health

# Database
docker compose exec postgres psql -U integrateai -c "SELECT 1"

# All services
make health
```

### Check Resource Usage

```bash
# Memory/CPU
docker stats

# Services running
docker compose ps
```

### Check Database

```bash
# Connect
docker compose exec postgres psql -U integrateai

# List tables
\dt

# Query example
SELECT COUNT(*) FROM documents;

# Exit
\q
```

---

## Common Issues & Fixes

### "Port Already In Use"
```bash
# Kill process
sudo lsof -i :8000
kill -9 <PID>

# Or use different port (edit docker-compose.yml)
```

### "Connection Refused"
```bash
# Wait longer (services take 30-40s to start)
sleep 30
make health

# Check logs
make logs
```

### "Out of Memory"
```bash
# Only run essential services
docker compose down
docker compose up -d app postgres

# Reduce chunk size
# Edit .env: CHUNK_SIZE=256
```

### "Frontend Can't Reach API"
```bash
# Check .env.local
cat providius-dashboard/.env.local

# Should point to your API
NEXT_PUBLIC_API_URL=http://localhost:8000

# Test API
curl http://localhost:8000/health
```

---

## TypeScript/React Patterns

### Using the API Client

```typescript
// In React component
'use client';  // Next.js client component
import { useState } from 'react';
import { chatApi } from '@/lib/api';

export default function ChatWidget() {
  const [message, setMessage] = useState('');
  const [response, setResponse] = useState('');

  const handleSend = async () => {
    const result = await chatApi.sendMessage({
      message,
      collection_id: 'collection-uuid',
    });
    setResponse(result.response);
  };

  return (
    <>
      <input value={message} onChange={(e) => setMessage(e.target.value)} />
      <button onClick={handleSend}>Send</button>
      {response && <p>{response}</p>}
    </>
  );
}
```

### Streaming Messages

```typescript
async function streamChat(message: string) {
  try {
    for await (const chunk of chatApi.streamMessage({
      message,
      collection_id: 'uuid',
    })) {
      console.log('Token:', chunk.token);
      console.log('Done:', chunk.done);
      if (chunk.sources) {
        console.log('Sources:', chunk.sources);
      }
    }
  } catch (error) {
    console.error('Stream failed:', error);
  }
}
```

---

## File Locations

| Need | File |
|------|------|
| Config | `.env` |
| API client | `providius-dashboard/lib/api.ts` |
| Chat UI | `providius-dashboard/src/components/Chat.tsx` |
| Routes | `app/api/v1/*.py` |
| Models | `app/models/*.py` |
| Prompts | `app/core/prompts/system.md` |
| Migrations | `migrations/versions/` |
| Tests | `evals/*.py` |
| Docs | `SETUP.md`, `DEPLOYMENT.md` |

---

## Production Checklist

- [ ] All tests passing: `make test`
- [ ] No lint errors: `make lint`
- [ ] Updated `.env` with production values
- [ ] Set `ENVIRONMENT=production`
- [ ] Generated new `SECRET_KEY`: `openssl rand -base64 32`
- [ ] Set real API keys (OpenAI, Anthropic, etc.)
- [ ] Configured database (AWS RDS, Azure, etc.)
- [ ] Set `DEBUG=false`
- [ ] Migrated database: `make migrate`
- [ ] Deployed: See [DEPLOYMENT.md](DEPLOYMENT.md)

---

## Useful Commands

```bash
# One-line status check
docker compose ps && curl -s http://localhost:8000/health | jq

# Tail logs with timestamps
docker compose logs -f --timestamps app

# Clean everything and start fresh
make down && make clean && make up

# Access database directly
docker compose exec postgres psql -U integrateai -d integrateai_db

# Restart just the app
docker compose restart app

# Update dependencies
cd providius-dashboard && npm update
uv sync --upgrade

# Type check frontend
cd providius-dashboard && npm run type-check

# Benchmark latency
make bench
```

---

## Key Files to Edit

**To customize behavior:**

| Config | File | How |
|--------|------|-----|
| API response format | `app/schemas/*.py` | Modify Pydantic models |
| Chat behavior | `app/core/prompts/system.md` | Edit system prompt |
| Database queries | `app/services/*.py` | Modify SQLAlchemy queries |
| LLM provider | `.env` `LLM_PROVIDER` | Switch provider |
| Frontend styling | `providius-dashboard/tailwind.config.js` | Customize theme |
| API endpoints | `app/api/v1/*.py` | Add/remove routes |

---

## External Resources

- **FastAPI Docs**: https://fastapi.tiangolo.com
- **Next.js Docs**: https://nextjs.org/docs
- **LangChain**: https://python.langchain.com
- **Prometheus**: https://prometheus.io/docs
- **PostgreSQL**: https://www.postgresql.org/docs

---

**Bookmark this page for quick reference!**

Last Updated: April 22, 2026
