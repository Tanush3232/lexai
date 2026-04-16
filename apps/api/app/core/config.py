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

    # Gemini
    GOOGLE_API_KEY: str = ""

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Embeddings
    EMBEDDING_MODEL: str = "models/gemini-embedding-2-preview"
    EMBEDDING_DIMENSION: int = 3072

    # LLM models
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_PRO_MODEL: str = "gemini-2.5-pro"
    MAX_CONTEXT_TOKENS: int = 100000

    # Agent settings
    AGENT_MAX_STEPS: int = 5
    AGENT_MAX_RETRIES: int = 2
    VECTORLESS_MAX_PAGES: int = 50


settings = Settings()
