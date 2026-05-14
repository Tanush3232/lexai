"""
Database initialization and session management (async SQLAlchemy + SQLModel)
"""
import sys
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import SQLModel

from app.core.config import settings


def _sql_echo() -> bool:
    """Only echo SQL in development AND when stdout can handle arbitrary Unicode."""
    if settings.APP_ENV != "development":
        return False
    enc = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
    return enc in ("utf8", "utf_8")


engine = create_async_engine(
    settings.POSTGRES_URL,
    echo=_sql_echo(),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db():
    """Create all tables on startup."""
    # Import all models to register them
    from app.models import user, folder, document, chat, draft, translation, audit  # noqa
    from app.models.clause import Clause  # noqa
    from app.models.llm_usage import LLMUsageLog  # noqa
    from app.models.legal_act import LegalAct, LegalActSeedLog  # noqa
    # Legal Ticketing System
    from app.models import ticket, message  # noqa
    from app.models.web_search import WebSearchSession, WebSearchCitation  # noqa
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)



async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI routes."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
