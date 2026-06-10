"""
Application configuration using pydantic-settings
"""
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    FRONTEND_URL: str = "http://localhost:3000"

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]

    # Database
    POSTGRES_URL: str = "postgresql+asyncpg://lexai:lexai@localhost:5433/lexai"
    POSTGRES_SYNC_URL: str = "postgresql://lexai:lexai@localhost:5433/lexai"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Qdrant
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "lexai_clauses"  # renamed from lexai_chunks

    # Elasticsearch
    ELASTICSEARCH_URL: str = "http://localhost:9200"
    ELASTICSEARCH_INDEX: str = "lexai_clauses"

    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "lexai_neo4j"

    # MinIO / S3
    MINIO_ENDPOINT: str = "http://localhost:9000"  # internal: backend → MinIO
    MINIO_PUBLIC_URL: str = ""                     # public: browser → MinIO (presigned URLs)
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "lexai-documents"

    # Network
    NETWORK_IP: str = ""  # LAN IP, e.g. 192.168.64.94

    # Auth
    JWT_SECRET: str = "change-this-secret"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # Gemini (used everywhere except contract drafting)
    GOOGLE_API_KEY: str = ""

    # Anthropic / Claude (used exclusively for contract drafting)
    ANTHROPIC_API_KEY: str = ""

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Embeddings
    EMBEDDING_MODEL: str = "models/gemini-embedding-2-preview"
    EMBEDDING_DIMENSION: int = 3072

    # LLM models
    GEMINI_MODEL: str = "gemini-2.5-pro"
    GEMINI_PRO_MODEL: str = "gemini-2.5-pro"
    MAX_CONTEXT_TOKENS: int = 200000

    # Agent settings
    AGENT_MAX_STEPS: int = 5
    AGENT_MAX_RETRIES: int = 2
    VECTORLESS_MAX_PAGES: int = 50

    # ── Email / Outlook (Legal Ticketing System) ──────────────────────────────
    EMAIL_HOST: str = "smtp.office365.com"
    EMAIL_PORT: int = 587
    EMAIL_USER: str = ""
    EMAIL_PASS: str = ""

    # ── Microsoft Graph API (Webhook / Ingestion) ──────────────────────────
    # Retained for backward compatibility; no longer used for active ingestion.
    GRAPH_TENANT_ID: str = ""        # Azure AD tenant ID
    GRAPH_CLIENT_ID: str = ""        # App (client) ID
    GRAPH_CLIENT_SECRET: str = ""    # Client secret value
    GRAPH_MAILBOX: str = ""          # e.g. app.info@adventz.com
    GRAPH_WEBHOOK_URL: str = ""      # e.g. https://api.yourdomain.com/api/v1/webhooks/graph
    GRAPH_WEBHOOK_SECRET: str = "lexai-webhook-secret"

    # ── SharePoint + Power Automate Ingestion ─────────────────────────────
    # Shared secret that Power Automate sends in X-SharePoint-Token header.
    # Set a long random string here and provide the same value in Power Automate.
    # If not set, falls back to GRAPH_WEBHOOK_SECRET for backward compatibility.
    SHAREPOINT_WEBHOOK_SECRET: str = ""  # Set in .env — if empty, falls back to GRAPH_WEBHOOK_SECRET
    # If True, uses the EMAIL_USER and EMAIL_PASS to get a Delegated token (avoids Admin Consent)
    GRAPH_USE_DELEGATED_AUTH: bool = True

    # ── Microsoft SSO (Entra ID / Azure AD) ──────────────────────────────────
    # Used for "Continue with Microsoft" login on the login page.
    # Only existing users (matched by email) can log in — no auto-provisioning.
    # Set all values in .env — never hardcode credentials here.
    AZURE_TENANT_ID: str = ""
    AZURE_CLIENT_ID: str = ""
    AZURE_CLIENT_SECRET: str = ""
    # Must match a Redirect URI registered in Azure Portal → Authentication
    # Local:  http://localhost:3000/auth/microsoft/callback
    # Prod:   https://YOURDOMAIN/auth/microsoft/callback
    AZURE_REDIRECT_URI: str = "http://localhost:3000/auth/microsoft/callback"


settings = Settings()
