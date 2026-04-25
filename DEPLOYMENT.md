# Providius — Deployment & Production Guide

> Production deployment guide for Providius AI Agents platform

---

## Pre-Deployment Checklist

- [ ] All tests passing: `make test && make eval`
- [ ] No linting errors: `make lint`
- [ ] Environment variables configured for production
- [ ] Database backups enabled
- [ ] Monitoring/alerts configured in Grafana
- [ ] API rate limits appropriate
- [ ] CORS origins configured for your domain
- [ ] Secret keys rotated and secured

---

## Production Configuration

### Environment Variables (.env)

```bash
# ── Security ──────────────────────────────────────────────────────────────────
ENVIRONMENT=production
DEBUG=false
SECRET_KEY=<generate-with-python: secrets.token_urlsafe(32)>
JWT_SECRET_KEY=<generate-with-python: secrets.token_urlsafe(32)>

# ── Database ──────────────────────────────────────────────────────────────────
# For AWS RDS, Azure Database, or managed PostgreSQL:
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dbname
POSTGRES_USER=produser
POSTGRES_PASSWORD=<strong-random-password>

# ── LLM Provider (choose one) ──────────────────────────────────────────────────
# Option 1: OpenAI (recommended for production)
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-<your-key>
OPENAI_MODEL=gpt-4o

# Option 2: Anthropic (better for privacy)
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<your-key>
ANTHROPIC_MODEL=claude-3-opus-20240229

# Option 3: Groq (fastest + cheapest)
LLM_PROVIDER=groq
GROQ_API_KEY=<your-key>
GROQ_MODEL=llama-3.1-70b-versatile

# ── Monitoring ────────────────────────────────────────────────────────────────
PROMETHEUS_ENABLED=true
LOG_LEVEL=INFO
LOG_FORMAT=json

# ── Rate Limiting ─────────────────────────────────────────────────────────────
RATE_LIMIT_PER_MINUTE=100
RATE_LIMIT_CHAT_PER_MINUTE=30

# ── Frontend (CORS) ───────────────────────────────────────────────────────────
# Allowed origins (no wildcards in production!)
CORS_ORIGINS=["https://app.providius.io","https://providius.io"]
```

---

## Docker Deployment

### 1. Production Docker Build

```bash
# Build image for production
docker build --target production -t providius-api:latest .

# Build with specific tag
docker build --target production -t providius-api:v1.0.0 .

# Push to registry
docker tag providius-api:latest myregistry.azurecr.io/providius-api:latest
docker push myregistry.azurecr.io/providius-api:latest
```

### 2. Docker Compose (Production)

Update `docker-compose.yml` for production:

```yaml
services:
  app:
    image: myregistry.azurecr.io/providius-api:v1.0.0  # Use image instead of build
    environment:
      ENVIRONMENT: production
    deploy:
      replicas: 2  # Multiple instances for redundancy
      resources:
        limits:
          cpus: '2'
          memory: 2G
    restart: always
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
```

Deploy:
```bash
docker compose -f docker-compose.yml up -d
```

---

## Cloud Deployment

### AWS Elastic Container Service (ECS)

```bash
# 1. Create ECR repository
aws ecr create-repository --repository-name providius-api --region us-east-1

# 2. Build and push
docker build -t providius-api:latest .
docker tag providius-api:latest 123456789.dkr.ecr.us-east-1.amazonaws.com/providius-api:latest
docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/providius-api:latest

# 3. Update ECS task definition with new image
# 4. Update service to use new task definition
```

### Azure Container Instances (ACI)

```bash
# 1. Push to Azure Container Registry
az acr build --registry myregistry --image providius-api:latest .

# 2. Deploy to ACI
az container create \
  --resource-group myresourcegroup \
  --name providius-api \
  --image myregistry.azurecr.io/providius-api:latest \
  --ports 8000 \
  --environment-variables \
    ENVIRONMENT=production \
    DATABASE_URL="postgresql+asyncpg://..." \
  --memory 2 \
  --cpu 1
```

### Railway.app (Recommended for Startups)

```bash
# 1. Install Railway CLI
npm i -g @railway/cli

# 2. Login and create project
railway login
railway init

# 3. Add PostgreSQL plugin
railway add --plugin postgresql

# 4. Deploy
railway up --detach

# 5. Configure environment via Railway dashboard
# 6. View logs: railway logs
```

### Heroku

```bash
# 1. Install Heroku CLI
# 2. Login
heroku login

# 3. Create app
heroku create providius-api

# 4. Add PostgreSQL
heroku addons:create heroku-postgresql:standard-0

# 5. Set environment variables
heroku config:set ENVIRONMENT=production
heroku config:set SECRET_KEY=<value>
heroku config:set LLM_PROVIDER=openai
heroku config:set OPENAI_API_KEY=<value>

# 6. Deploy
git push heroku main

# 7. Check logs
heroku logs --tail
```

---

## Database Management

### PostgreSQL Backup

```bash
# Create backup
pg_dump -h localhost -U integrateai -d integrateai_db > backup.sql

# Restore backup
psql -h localhost -U integrateai -d integrateai_db < backup.sql

# With Docker
docker compose exec postgres pg_dump -U integrateai integrateai_db > backup.sql
```

### Migrations in Production

```bash
# Before deploying new code
docker compose exec app alembic upgrade head

# Or with Kubernetes
kubectl exec -it pod/providius-app-xyz -- alembic upgrade head
```

---

## Monitoring & Observability

### Prometheus

Check metrics endpoint:
```bash
curl http://localhost:9090/api/v1/query?query=http_requests_total
```

Key metrics to monitor:
- `http_requests_total` — Request count by status code
- `http_request_duration_seconds` — Request latency (p50, p95, p99)
- `llm_tokens_total` — Total tokens used (cost tracking)
- `rag_hallucination_score` — Hallucination detection (alert if > 0.3)
- `vector_store_search_ms` — Retrieval latency

### Grafana Alerts

Set up alerts in Grafana:
1. Go to http://grafana.yourserv er.com/alerts
2. Create new alert rule:
   - Condition: `http_request_duration_seconds_p95 > 5`
   - For: 5m
   - Action: Notify via Slack/PagerDuty

### Logs

For JSON logs (production):
```bash
# Parse and query with jq
docker compose logs app | jq 'select(.level=="ERROR")'

# Or stream to log aggregation
# - Datadog
# - CloudWatch
# - Loki
# - ELK Stack
```

---

## Performance Tuning

### Database

```sql
-- Create indexes for common queries
CREATE INDEX idx_documents_user_id ON documents(user_id);
CREATE INDEX idx_chunks_doc_id ON document_chunks(document_id);
CREATE INDEX idx_chunks_embedding ON document_chunks USING ivfflat(embedding vector_cosine_ops);

-- Connection pooling
-- Use PgBouncer: pgbouncer.ini
[databases]
integrateai_db = host=prod-postgres port=5432 user=produser password=secret

[pgbouncer]
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 25
```

### API

```bash
# Run multiple workers
gunicorn app.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --access-logfile - \
  --error-logfile -

# Or configure in docker-compose.yml
CMD ["gunicorn", "app.main:app", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker"]
```

### Caching

Add Redis for caching:
```python
# In docker-compose.yml
redis:
  image: redis:7-alpine
  
# In app/core/cache.py (new)
from redis import Redis
cache = Redis(host='redis', port=6379)

# Cache chat responses
@router.post("/chat")
async def chat(request: ChatRequest):
    cache_key = f"chat:{request.query}:{request.collection_id}"
    if cached := cache.get(cache_key):
        return json.loads(cached)
    # ... process request ...
    cache.setex(cache_key, 3600, json.dumps(response))
    return response
```

---

## Security Best Practices

### API Keys

```bash
# Rotate keys regularly
# Store in:
# - AWS Secrets Manager
# - Azure Key Vault
# - HashiCorp Vault
# - 1Password / Bitwarden

# Never log sensitive values
# In app/core/logging.py
class SensitiveFormatter(logging.Formatter):
    def format(self, record):
        record.msg = re.sub(r'sk-[a-zA-Z0-9]+', 'sk-***', str(record.msg))
        return super().format(record)
```

### CORS Configuration

```python
# In app/core/middleware.py
CORS_ORIGINS = settings.CORS_ORIGINS  # ["https://app.providius.io"]
allow_credentials = True
allow_methods = ["GET", "POST", "PUT", "DELETE"]
allow_headers = ["Content-Type", "Authorization"]
```

### Rate Limiting

Already configured:
- `RATE_LIMIT_PER_MINUTE=100` — Global
- `RATE_LIMIT_CHAT_PER_MINUTE=30` — Per-user chat

Adjust in `.env` for production load.

### HTTPS/TLS

```nginx
# nginx reverse proxy example
upstream api {
    server app:8000;
}

server {
    listen 80;
    server_name api.providius.io;
    
    # Redirect HTTP → HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.providius.io;
    
    ssl_certificate /etc/letsencrypt/live/api.providius.io/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.providius.io/privkey.pem;
    
    location / {
        proxy_pass http://api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## Scaling

### Horizontal Scaling (Multiple Instances)

```yaml
# kubernetes example
apiVersion: apps/v1
kind: Deployment
metadata:
  name: providius-api
spec:
  replicas: 3  # Scale to 3 instances
  selector:
    matchLabels:
      app: providius-api
  template:
    metadata:
      labels:
        app: providius-api
    spec:
      containers:
      - name: api
        image: myregistry.azurecr.io/providius-api:latest
        ports:
        - containerPort: 8000
        resources:
          limits:
            memory: "2Gi"
            cpu: "2"
          requests:
            memory: "1Gi"
            cpu: "1"
```

### Load Balancing

```bash
# Configure load balancer to distribute traffic
# - AWS ALB (Application Load Balancer)
# - Azure Load Balancer
# - Kubernetes Service (type: LoadBalancer)
# - Nginx Upstream
```

---

## Disaster Recovery

### Database Backup Strategy

```bash
# Daily backups (cron)
0 2 * * * pg_dump -h dbhost -U user db > /backups/db-$(date +%Y-%m-%d).sql

# Keep 30 days
find /backups -name "db-*.sql" -mtime +30 -delete

# Test restore monthly
pg_restore -h test-db -U user -d testdb /backups/db-latest.sql
```

### Failover

```bash
# Set up PostgreSQL replication or use managed database with failover
# AWS RDS with Multi-AZ
# Azure Database with geo-replication
# All provide automatic failover
```

---

## Cost Optimization

### Database
- Use `t3.micro` / `t4g.micro` for low traffic
- Reserved instances for production (30% discount)
- Scale down non-prod databases at night

### Compute
- Use spot instances (70% cheaper)
- Scale down replicas based on traffic patterns
- Use auto-scaling groups

### LLM Provider
- Switch to Groq for 10x cheaper inference
- Cache common responses in Redis
- Implement request batching

### Storage
- Archive old data to S3/Blob Storage
- Compress database backups
- Clean up old embeddings

---

## Monitoring Commands

```bash
# Check API health
curl -f http://api.providius.io/health

# Monitor memory usage
docker compose stats

# Check database connections
psql -h db -U user -c "SELECT count(*) FROM pg_stat_activity;"

# View error rate
curl http://prometheus:9090/api/v1/query?query=http_requests_total{status=~\"5..\"} | jq

# Check vector store size
psql -h db -U user -c "SELECT pg_size_pretty(pg_total_relation_size('document_chunks'));"
```

---

## Troubleshooting Production Issues

### API Timeout

1. Check database: `make health`
2. Check LLM API status
3. Increase deployment resources:
   - Edit `docker-compose.yml`
   - Increase `memory` limit
   - Increase `cpus` limit

### High Memory Usage

1. Check `TOP_K_RETRIEVE` setting (lower = less memory)
2. Enable Prometheus metrics: `PROMETHEUS_ENABLED=false` (for metric calculation)
3. Add memory limit: `deploy.resources.limits.memory: 4G`

### Database Errors

1. Check connections: `SELECT count(*) FROM pg_stat_activity;`
2. Increase `max_connections` in PostgreSQL config
3. Use connection pooling (PgBouncer)

---

## Support & Resources

- **Documentation**: https://docs.providius.io
- **Status Page**: https://status.providius.io
- **API Health**: http://api.providius.io/health
- **Support Email**: support@providius.io

---

*Last Updated: April 22, 2026 — Providius Team*
