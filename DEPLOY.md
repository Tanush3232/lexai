# LexAI — Production Deployment Guide (AWS Lightsail)
> **Last updated:** 2026-05-13  
> **Branch:** `staging`  
> **Deployed stack:** FastAPI · Celery · Next.js · Nginx · Postgres · Redis · Qdrant · Elasticsearch · Neo4j · MinIO

---

## What's in This Push (New vs Production)

The following changes are **not yet in production** and will go live with this deployment.

### 🆕 New Files (Untracked — must commit)
| Path | What it is |
|------|-----------|
| `apps/api/alembic/versions/002_web_search.py` | DB migration: creates `web_search_sessions` + `web_search_citations` tables (with `turn_index`) |
| `apps/api/app/ai/web_search_orchestrator.py` | Core AI orchestrator for legal web search (fast/pro/deep modes) |
| `apps/api/app/api/v1/web_search.py` | API router: SSE streaming search, session CRUD |
| `apps/api/app/api/v1/webhooks.py` | Microsoft Graph webhook handler for email ticketing |
| `apps/api/app/models/message.py` | Message model (Legal Ticketing) |
| `apps/api/app/models/ticket.py` | Ticket + TicketUser + EmailThread models |
| `apps/api/app/models/web_search.py` | WebSearchSession + WebSearchCitation models |
| `apps/api/app/services/graph_webhook_service.py` | MS Graph subscription management |
| `apps/api/app/services/legal_email_service.py` | Outlook SMTP email sender |
| `apps/api/app/services/message_service.py` | Ticket message CRUD |
| `apps/api/app/services/ticket_ingestion_service.py` | Email→ticket ingestion logic |
| `apps/api/app/services/ticket_notification_service.py` | Ticket email notifications |
| `apps/api/app/services/ticket_service.py` | Ticket CRUD service |
| `apps/api/app/tasks/webhook_tasks.py` | Celery tasks for webhook processing |
| `apps/api/migrate.py` | One-off migration helper (superseded by alembic 002) |
| `apps/web/app/dashboard/web-search/` | Full Web Search UI (list, new, session pages) |

### 📝 Modified Files
| Path | Key change |
|------|-----------|
| `apps/api/alembic/env.py` | Added `web_search` model import so alembic detects new tables |
| `apps/api/app/ai/gemini_client.py` | Stability improvements |
| `apps/api/app/ai/prompts.py` | Updated translation/OCR prompts |
| `apps/api/app/ai/workflows/translation_workflow.py` | Translation pipeline stability |
| `apps/api/app/api/v1/translations.py` | Translation API improvements |
| `apps/api/app/celery_app.py` | ✅ Fixed: `worker_pool` changed from `solo` (Windows-only) → `prefork` (Linux production) |
| `apps/api/app/core/config.py` | Added MS Graph, email, and webhook settings |
| `apps/api/app/core/database.py` | Minor connection pool tuning |
| `apps/api/app/main.py` | Registered `webhooks` + `web_search` routers |
| `apps/api/app/models/__init__.py` | Added `ticket`, `message`, `web_search` model exports |
| `apps/api/requirements.txt` | Added `aiosmtplib` (async SMTP) |
| `apps/web/app/dashboard/layout.tsx` | Added Web Search nav item |
| `apps/web/app/dashboard/translations/page.tsx` | Translation UI improvements |
| `apps/web/app/globals.css` | UI style updates |
| `apps/web/lib/api.ts` | Added web search + ticket API calls |

### 🔧 Config Fixes Applied Before Push
| File | Fix |
|------|-----|
| `nginx/nginx.conf` | Added `proxy_buffering off; proxy_cache off;` to `/api/` — **required for SSE streaming** |
| `apps/api/app/celery_app.py` | Changed `worker_pool="solo"` → `worker_pool="prefork"` — **required on Linux** |
| `apps/api/alembic/env.py` | Added `web_search` model import — **required for migrations to detect new tables** |

---

## Part 1 — Push to GitHub (Local Machine)

Run in PowerShell from the project root:

```powershell
cd "C:\Users\tanush.angrish\Desktop\Tanush\LexAI"

# Stage all new and modified files
git add .

# Sanity check — .env must NOT appear in this list
git status

# Commit
git commit -m "feat: web search + legal ticketing + translation pipeline stability"

# Push to staging branch (your remote branch)
git push origin staging
```

> ⚠️ If `.env` appears in `git status`, run `git rm --cached .env` first.

---

## Part 2 — Deploy on AWS Lightsail

### 2a. SSH into the instance

```bash
ssh -i ~/Downloads/LexAi.pem ubuntu@<YOUR_LIGHTSAIL_PUBLIC_IP>
```

### 2b. Pull the latest code

```bash
cd ~/LexAI   # or wherever you cloned it

# Pull latest from staging
git pull origin staging
```

### 2c. Verify `.env` is correct

```bash
# Open and check these values are set correctly
nano .env
```

Ensure these are filled in (not placeholder values):

```ini
SERVER_IP=<your Lightsail public IP>
GOOGLE_API_KEY=<your Gemini API key>
JWT_SECRET=<long random hex — openssl rand -hex 32>
POSTGRES_PASSWORD=<strong password>
MINIO_ACCESS_KEY=<strong key>
MINIO_SECRET_KEY=<strong secret>
MINIO_PUBLIC_URL=http://<SERVER_IP>:9000
NEXT_PUBLIC_API_URL=http://<SERVER_IP>
CORS_ORIGINS=["http://<SERVER_IP>", "http://localhost:3000"]
NEO4J_PASSWORD=<strong password>

# Legal Ticketing (only if using email ingestion):
EMAIL_USER=<outlook mailbox>
EMAIL_PASS=<app password>
GRAPH_TENANT_ID=<azure tenant id>
GRAPH_CLIENT_ID=<azure app id>
GRAPH_CLIENT_SECRET=<azure client secret>
GRAPH_MAILBOX=<watched mailbox email>
GRAPH_WEBHOOK_URL=http://<SERVER_IP>/api/v1/webhooks/graph
```

### 2d. Rebuild and restart all services

```bash
# Rebuild changed images (api, worker, web) and restart everything
docker compose up -d --build

# Watch startup logs — wait until all services are healthy
docker compose logs -f --tail=60
```

> ⏱️ First build after pulling code changes typically takes **3–8 minutes**.

### 2e. Check all containers are healthy

```bash
docker compose ps
```

Expected output (all should be `running` or `healthy`):

```
NAME                   STATUS
lexai_postgres         healthy
lexai_redis            healthy
lexai_qdrant           running
lexai_elasticsearch    healthy
lexai_neo4j            running
lexai_minio            healthy
lexai_api              running
lexai_worker           running
lexai_web              running
lexai_nginx            running
```

> 🕐 Elasticsearch takes up to 90 seconds to become healthy. Wait for it before proceeding.

### 2f. Run database migrations

**Run ONCE per deployment when there are schema changes.** This push adds the Web Search and Legal Ticketing tables.

```bash
# This runs all pending alembic migrations in order:
#   001_legal_ticketing  → tickets, ticket_users, email_threads, email_logs, messages
#   002_web_search       → web_search_sessions, web_search_citations (with turn_index)
docker compose exec api alembic upgrade head
```

Expected output:
```
INFO  [alembic.runtime.migration] Running upgrade  -> 001_legal_ticketing, add_legal_ticketing_system
INFO  [alembic.runtime.migration] Running upgrade 001_legal_ticketing -> 002_web_search, add_web_search_feature
```

> ⚠️ If the database already has these tables (from a previous manual migration), alembic will detect them as already applied and skip safely.

### 2g. Verify the deployment

Open in your browser:
- **App**: `http://<YOUR_LIGHTSAIL_PUBLIC_IP>/`
- **API health**: `http://<YOUR_LIGHTSAIL_PUBLIC_IP>/api/health`
- **API docs**: `http://<YOUR_LIGHTSAIL_PUBLIC_IP>/api/docs`

Test the web search endpoint:
```bash
curl http://<SERVER_IP>/api/health
# Expected: {"status":"ok","service":"lexai-api"}
```

---

## Part 3 — Open Firewall Ports (First Time Only)

In **AWS Lightsail Console → Your Instance → Networking → Add rule**:

| Port | Protocol | Purpose |
|------|----------|---------|
| 80   | TCP      | Main app (Nginx — frontend + API) |
| 9000 | TCP      | MinIO file downloads (presigned PDF URLs) |
| 22   | TCP      | SSH (already open by default) |

---

## Part 4 — Future Re-Deployments

```bash
# LOCAL: commit and push changes
git add .
git commit -m "your change description"
git push origin staging

# LIGHTSAIL: pull and rebuild
ssh -i ~/Downloads/LexAi.pem ubuntu@<SERVER_IP>
cd ~/LexAI
git pull origin staging
docker compose up -d --build

# Only run migrations if schema changed:
docker compose exec api alembic upgrade head
```

---

## Part 5 — Useful Commands on Lightsail

```bash
# ── Container status ──────────────────────────────────────────────────────────
docker compose ps
docker stats --no-stream

# ── Logs ──────────────────────────────────────────────────────────────────────
docker compose logs -f api          # API logs
docker compose logs -f worker       # Celery worker logs
docker compose logs -f web          # Next.js logs
docker compose logs -f nginx        # Nginx access/error logs

# ── Restart a single service (no rebuild) ─────────────────────────────────────
docker compose restart api
docker compose restart worker
docker compose restart nginx

# ── Migrations ────────────────────────────────────────────────────────────────
docker compose exec api alembic upgrade head       # apply all pending migrations
docker compose exec api alembic current            # show current revision
docker compose exec api alembic history            # show migration history

# ── Database access ───────────────────────────────────────────────────────────
docker compose exec postgres psql -U lexai -d lexai

# ── Memory check (critical on 2 GB Lightsail) ────────────────────────────────
free -h
docker stats --no-stream

# ── Clean up Docker build cache (free disk space) ────────────────────────────
docker system prune -f
docker builder prune -f

# ── Full stop ─────────────────────────────────────────────────────────────────
docker compose down

# ── DANGER: wipes all data volumes ───────────────────────────────────────────
docker compose down -v
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| App not loading | `docker compose logs nginx` — check for upstream errors |
| API 502/503 | `docker compose logs api` — check if api container crashed |
| Web search SSE hangs | Check `nginx/nginx.conf` has `proxy_buffering off` in `/api/` block |
| Celery worker not processing | `docker compose logs worker` — check Redis connectivity |
| Elasticsearch red status | `docker compose restart elasticsearch` — wait 90s |
| MinIO PDFs not loading | Ensure port 9000 open in Lightsail firewall; check `MINIO_PUBLIC_URL` in `.env` |
| Migration already applied error | `docker compose exec api alembic current` — alembic tracks applied versions |
| OOM crash | `free -h` + `docker stats`. Restart elasticsearch first: `docker compose restart elasticsearch` |
| Build fails (disk full) | `docker system prune -f` then `docker compose up -d --build` |

---

## Architecture

```
Browser
  │
  └─ :80 → Nginx ─┬─ /       → web:3000   (Next.js frontend)
                   ├─ /api/   → api:8000   (FastAPI backend)
                   │              └─ /api/v1/web-search/search → SSE stream (proxy_buffering off)
                   └─ /api/docs → api:8000/api/docs (Swagger)

Browser
  └─ :9000 → MinIO (direct, presigned PDF downloads)

Internal Docker network only:
  api + worker → postgres:5432, redis:6379, qdrant:6333,
                 elasticsearch:9200, neo4j:7687, minio:9000
```
