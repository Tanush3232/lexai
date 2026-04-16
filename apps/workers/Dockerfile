# Workers share the full API codebase (same ingestion pipeline, same models)
# ── Build stage ────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Use the API's complete requirements (workers need the same deps)
COPY apps/api/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# Copy the API app (shared code: ingestion pipeline, models, core services)
COPY apps/api/ .

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Run both queues in a single worker container (scale separately in prod)
CMD ["celery", "-A", "app.celery_app", "worker", \
     "--queues=ingestion,translation", \
     "--loglevel=info", \
     "--concurrency=2", \
     "--max-tasks-per-child=50"]
