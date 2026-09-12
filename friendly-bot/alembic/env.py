"""Run the F01 Alembic revision sequence through the asyncpg driver."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, async_engine_from_config

import friendly_bot.persistence.models  # noqa: F401
from alembic import context
from friendly_bot.config.settings import DatabaseSettings
from friendly_bot.persistence.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure_url() -> dict[str, Any]:
    """Read the required async database URL without persisting it in Alembic config."""

    settings = DatabaseSettings()
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = str(settings.database_url)
    return configuration


def run_migrations_offline() -> None:
    """Render SQL from the configured asynchronous URL without opening a connection."""

    context.configure(
        url=_configure_url()["sqlalchemy.url"],
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    """Configure Alembic on the synchronous bridge connection."""

    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations(connectable: AsyncEngine) -> None:
    """Bridge Alembic's synchronous operations through an asyncpg engine."""

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(_run_sync_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Apply revisions against the required DatabaseSettings URL."""

    configuration = _configure_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    asyncio.run(_run_async_migrations(connectable))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
