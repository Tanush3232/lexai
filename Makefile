# LexAI Makefile
.PHONY: dev infra stop logs api web workers migrate seed

# Start all infrastructure (Postgres, Redis, Qdrant, Neo4j, MinIO)
infra:
	docker compose -f infra/docker-compose.yml up -d
	@echo "Waiting for services to be healthy..."
	@sleep 5

# Run database migrations
migrate:
	cd apps/api && python -m alembic upgrade head

# Seed initial data (templates, clause library)
seed:
	cd apps/api && python scripts/seed.py

# Start API server
api:
	cd apps/api && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Start Celery workers
workers:
	cd apps/api && celery -A app.celery_app worker --loglevel=info -P solo

# Start frontend
web:
	cd apps/web && npm run dev

# Full local dev (requires Windows terminal with multiple panes, or use this sequentially)
dev: infra migrate
	@echo "Infrastructure started. Run these in separate terminals:"
	@echo "  make api      - FastAPI backend"
	@echo "  make workers  - Celery workers"
	@echo "  make web      - Next.js frontend"

# Stop all infrastructure
stop:
	docker compose -f infra/docker-compose.yml down

# View logs
logs:
	docker compose -f infra/docker-compose.yml logs -f

# Install all dependencies
install:
	cd apps/api && pip install -r requirements.txt
	cd apps/workers && pip install -r requirements.txt
	cd apps/web && npm install

# Run tests
test:
	cd apps/api && pytest tests/ -v

# Format and lint
lint:
	cd apps/api && ruff check . && black --check .
	cd apps/web && npm run lint
