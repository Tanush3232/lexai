"""
Alembic environment for async SQLAlchemy migrations.
"""
import asyncio
from logging.config import fileConfig
from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

# Import all models so they register with SQLModel metadata
from app.models import user, folder, document, chat, draft, translation, audit  # noqa
from app.models.legal_act import LegalAct, LegalActSeedLog  # noqa
# Legal Ticketing System models
from app.models import ticket, message  # noqa
# Web Search models
from app.models import web_search  # noqa
from app.core.config import settings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline():
    url = settings.POSTGRES_SYNC_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    engine = create_async_engine(settings.POSTGRES_URL, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online():
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
