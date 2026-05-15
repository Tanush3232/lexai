"""
LexAI FastAPI Application Entry Point
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.core.logging import setup_logging
from app.core.storage import init_storage
from app.core.vector_store import init_vector_store
from app.core.graph_db import init_graph_db
from app.core.elasticsearch import init_elasticsearch

from app.api.v1 import (
    auth,
    folders,
    documents,
    chat,
    drafts,
    translations,
    audit,
    clauses,
    usage,
    acts,
    users,
    webhooks,          # ← Legal Ticketing — Graph webhook
    web_search,        # ← Web Search Feature
    tickets,
)


setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all connections on startup."""
    import asyncio
    await init_db()
    await init_storage()
    await _init_legal_acts_bucket()
    # Non-blocking inits with 10s timeout — server starts even if these are slow
    for name, coro in [("vector_store", init_vector_store()), ("elasticsearch", init_elasticsearch())]:
        try:
            await asyncio.wait_for(coro, timeout=10)
        except asyncio.TimeoutError:
            pass  # logged inside the init functions
        except Exception:
            pass
    try:
        init_graph_db()
    except Exception:
        pass
    yield


async def _init_legal_acts_bucket():
    """Create the legal-acts MinIO bucket if it doesn't exist."""
    import asyncio
    from minio.error import S3Error
    from app.core.storage import get_storage_client

    def _create():
        client = get_storage_client()
        if not client.bucket_exists("legal-acts"):
            client.make_bucket("legal-acts")

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _create)
    except Exception:
        pass  # non-fatal


app = FastAPI(
    title="LexAI — Legal Operations Platform",
    description="Production-grade legal AI powered by Gemini",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(folders.router, prefix="/api/v1/folders", tags=["folders"])
app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
app.include_router(clauses.router, prefix="/api/v1/clauses", tags=["clauses"])
app.include_router(drafts.router, prefix="/api/v1/drafts", tags=["drafts"])
app.include_router(translations.router, prefix="/api/v1/translations", tags=["translations"])
app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"])
app.include_router(usage.router, prefix="/api/v1/usage", tags=["usage"])
app.include_router(acts.router, prefix="/api/v1/acts", tags=["acts"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(tickets.router, prefix="/api/v1/tickets", tags=["tickets"])
# Legal Ticketing — Graph webhook (no auth — Microsoft calls this directly)
app.include_router(webhooks.router, prefix="/api/v1/webhooks/graph", tags=["webhooks"])
# Web Search Feature
app.include_router(web_search.router, prefix="/api/v1/web-search", tags=["web-search"])

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "lexai-api"}
