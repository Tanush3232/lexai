# LexAI Project File Structure

This document provides a organized overview of the LexAI project codebase, excluding dependency and build directories (like `.venv`, `node_modules`, `.next`, etc.).

## 📂 Root Directory
| File / Folder | Description |
| :--- | :--- |
| `apps/` | Core application services (API, Web, Workers) |
| `docs/` | Project documentation and guides |
| `infra/` | Infrastructure configuration (Docker, etc.) |
| `nginx/` | Nginx reverse proxy configurations |
| `docker-compose.yml` | Production Docker orchestration |
| `DEPLOY.md` | Deployment instructions |
| `WIFI_IP_UPDATE.md` | Guide for updating local network IPs |
| `.env.example` | Template for environment variables |
| `Makefile` | Utility commands for project management |

---

## 🚀 Apps

### 1. Backend API (`apps/api/`)
The FastAPI-based backend handling business logic, AI orchestration, and database management.

- `app/`
    - `ai/`: Gemini AI clients and prompts
    - `api/`: API route definitions (v1)
    - `core/`: Core configurations (DB, logic, logging, storage)
    - `ingestion/`: Document processing and indexing pipeline
    - `models/`: Database models (SQLModel)
    - `services/`: Business logic services
    - `tasks/`: Celery asynchronous task definitions
- `scripts/`: Initialization and seeding scripts
- `alembic/`: Database migration files
- `batch_download.py`: Script to download acts from IndiaCode
- `bulk_ingest.py`: Bulk document ingestion script
- `Dockerfile`: Container definition for the API service

### 2. Web Frontend (`apps/web/`)
The Next.js frontend providing the user interface.

- `app/`: Next.js App Router (Dashboard, Login, Register)
- `public/`: Static assets (images, icons)
- `lib/`: Shared utilities and UI components
- `next.config.ts`: Next.js configuration
- `tailwind.config.ts`: Tailwind CSS styling configuration
- `Dockerfile`: Container definition with standalone output optimization

### 3. Workers (`apps/workers/`)
Background workers for handling heavy lifting like translations and ingestion.

- `Dockerfile`: Environment for running Celery workers

---

## 🛠️ Infrastructure & DevOps
- `infra/`: Monitoring and state management configurations.
- `nginx/nginx.conf`: Routes traffic between Web, API, and MinIO.
- `docker-compose.yml`: Defines the 10+ services (Postgres, Redis, Qdrant, ES, Neo4j, MinIO, etc.) used by LexAI.

## 📝 Miscellaneous Scripts
- `patch_v3.py`: Latest Gemini OCR and ingestion logic patch.
- `test_cors.py`: Utility to verify network accessibility.
- `start_all.py`: Script to launch development environments.
