# IntegrateAI Blueprint — AGENTS.md

> Internal documentation for the 3-person IntegrateAI agency team.
> This file explains the architecture, how to deliver a client integration, and how to extend the system.

---

## What This Is

The **IntegrateAI Blueprint** is a reusable, production-ready framework that lets us deliver RAG + Agentic AI integrations in **5–10 business days** for SMEs across five verticals:

| Vertical | Common Use Case |
|---|---|
| Finance | Policy Q&A, compliance document search, report analysis |
| Healthcare | Clinical protocol lookup, patient FAQ bots |
| Manufacturing | SOP retrieval, maintenance manual Q&A |
| Retail | Product catalogue search, returns policy bots |
| Logistics | Route optimization Q&A, regulatory compliance |

---

## The RAG Foundation Pack (Included)

The base framework ships with:

```
User Query
    │
    ▼
[Node 1: Retrieve]
  └─ Hybrid search (dense + BM25) against pgvector
  └─ Metadata filtering (by date, department, doc type)
    │
    ▼
[Node 2: Rerank]
  └─ Cohere Rerank API (or score-based fallback)
  └─ Select top-5 most relevant chunks
    │
    ▼
[Node 3: Generate]
  └─ Multi-provider LLM (OpenAI / Anthropic / Groq)
  └─ Grounded answer with source citations
  └─ Streaming tokens via SSE
    │
    ▼
[Node 4: Validate]
  └─ Hallucination guard (word-overlap + configurable LLM judge)
  └─ Retry loop (max 1 retry)
  └─ Prometheus metric emission
```

---

## How to Deliver a Client Integration (5–10 Day Playbook)

### Day 1–2: Environment Setup
```bash
git clone https://github.com/your-org/integrateai-blueprint client-name-ai
cd client-name-ai
cp .env.example .env          # Fill in API keys
make up                       # Start full stack
```

### Day 2–3: Document Ingestion
1. Create a collection via `POST /api/v1/collections` with appropriate `vertical`
2. Upload client documents via `POST /api/v1/documents/upload`
3. Monitor indexing status via `GET /api/v1/documents/{id}`

### Day 3–5: Tuning
- Adjust `CHUNK_SIZE` and `CHUNK_OVERLAP` in `.env` for the document type
- Tune `SIMILARITY_THRESHOLD` — lower for recall, higher for precision
- Edit `app/core/prompts/system.md` for the client's domain and tone
- Add metadata filters to restrict search to relevant subsets

### Day 5–8: Frontend Integration
- Pass the collection UUID and JWT token from auth flow
- Implement SSE streaming: listen for `token`, `sources`, `done` events
- Display sources with `doc_title` and `relevance_score`

### Day 8–10: QA + Handover
- Run `make eval` to benchmark accuracy and hallucination rate
- Check Grafana dashboard for latency, error rate, token usage
- Hand over API docs at `GET /docs`

---

## Adding New Packs (Extending the Graph)

The LangGraph pipeline is designed to accept new nodes without touching existing ones.

### Agentic Workflow Pack
```python
# In app/core/langgraph/graph.py

from app.core.langgraph.tools import web_search_tool, sql_query_tool

graph.add_node("tool_call", tool_call_node)
graph.add_node("tool_result", tool_result_node)

# Insert between generate and validate
graph.add_edge("generate", "tool_call")
graph.add_edge("tool_call", "tool_result")
graph.add_edge("tool_result", "validate")
```

### Compliance Pack (Finance / Healthcare)
- Add a `compliance_check_node` after `validate`
- Load regulatory rules from a separate "compliance" collection
- Block responses that violate GDPR, HIPAA, FCA rules

### Monitoring Dashboard Pack
- Add custom Grafana panels to `grafana/dashboards/integrateai.json`
- Emit additional Prometheus metrics from `app/core/metrics.py`
- Build client-facing usage dashboards

---

## Multi-LLM Provider Switching

Switch via a single environment variable — no code changes:

```bash
# .env
LLM_PROVIDER=openai      # Default: cheap + fast + good
LLM_PROVIDER=anthropic   # Better for long documents, compliance contexts
LLM_PROVIDER=groq        # Fastest (Llama 3.1 70B at ~300 tok/s) — good for demos
```

Embeddings always use OpenAI `text-embedding-3-small` for index consistency.
Changing the embedding model requires re-indexing all documents.

---

## Team Responsibilities

| Role | Responsibilities |
|---|---|
| **Dev (you)** | Backend, infrastructure, LangGraph nodes, client integrations |
| **Designer** | React frontend, SSE streaming UI, Grafana dashboard polish |
| **Marketer** | API documentation, client onboarding, demo scripts |

---

## Environment Variables Quick Reference

| Variable | Purpose | Default |
|---|---|---|
| `LLM_PROVIDER` | Active LLM provider | `openai` |
| `CHUNK_SIZE` | Tokens per chunk | `512` |
| `TOP_K_RETRIEVE` | Candidates before reranking | `10` |
| `TOP_K_FINAL` | Final sources returned | `5` |
| `SIMILARITY_THRESHOLD` | Min relevance score | `0.7` |
| `ENABLE_HYBRID_SEARCH` | Dense + BM25 | `true` |
| `RERANK_TOP_N` | Chunks after Cohere rerank | `5` |

---

## Observability

- **Logs**: Structured JSON at stdout, queryable in production via CloudWatch/Datadog
- **Metrics**: Prometheus at `:9090`, Grafana at `:3000`
- **Traces**: Add `opentelemetry-sdk` exporter for distributed tracing (Jaeger/Tempo)
- **Alerts**: Configure Grafana alerting on hallucination_score > 0.5 or latency p95 > 5s

---

*Last updated: IntegrateAI Team*
