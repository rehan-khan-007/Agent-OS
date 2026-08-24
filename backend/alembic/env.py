"""
Alembic environment config, written for this project's async
SQLAlchemy setup (asyncpg) — Alembic's default generated env.py
assumes a sync engine, which doesn't work with this project's
async_session/create_async_engine setup without this adaptation.

Reads the database URL from the app's own settings (app.config)
rather than duplicating it in alembic.ini, so there's exactly one
place DATABASE_URL is configured.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# Import Base and every model module so their tables are registered
# on Base.metadata before Alembic compares against it — the same
# requirement noted in scripts/setup_database.py.
from app.database import Base
from app.config import settings
from app.retrieval.models import DocumentChunk  # noqa: F401
from app.memory.models import ConversationMessage  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generates SQL scripts without a live DB connection (not used
    in this project's normal workflow, but kept for completeness —
    it's part of Alembic's standard generated setup)."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(settings.database_url, poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
