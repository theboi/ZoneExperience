"""PostgreSQL session-lock integration contracts for the Telegram runtime."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import asyncpg
import pytest
from pydantic import PostgresDsn

from friendly_bot.persistence import DirectPostgresConnectionFactory
from friendly_bot.telegram.runtime_lock import (
    TelegramRuntimeAlreadyRunningError,
    TelegramRuntimeLock,
    TelegramRuntimeLockLostError,
)


@dataclass
class Postgres:
    """Open direct, non-pooled PostgreSQL connections for one integration test."""

    connection_factory: DirectPostgresConnectionFactory
    connections: list[asyncpg.Connection[asyncpg.Record]] = field(default_factory=list)

    async def connect(self) -> asyncpg.Connection[asyncpg.Record]:
        connection = await self.connection_factory()
        self.connections.append(connection)
        return connection

    async def close_all(self) -> None:
        for connection in self.connections:
            if not connection.is_closed():
                await connection.close()


@pytest.fixture
async def postgres() -> AsyncIterator[Postgres]:
    """Use the configured test database without creating an ORM/session boundary."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")
    instance = Postgres(DirectPostgresConnectionFactory(PostgresDsn(database_url)))
    try:
        yield instance
    finally:
        await instance.close_all()


async def test_second_runtime_cannot_acquire_fixed_session_lock(
    postgres: Postgres,
) -> None:
    """A second process must not poll or send while the first runtime owns the session."""

    async with await TelegramRuntimeLock.acquire(postgres.connect):
        with pytest.raises(TelegramRuntimeAlreadyRunningError):
            await TelegramRuntimeLock.acquire(postgres.connect)


async def test_runtime_lock_health_fails_without_reacquiring_after_connection_loss(
    postgres: Postgres,
) -> None:
    """A lost lock session must halt long-poll work instead of quietly taking a new lock."""

    lock = await TelegramRuntimeLock.acquire(postgres.connect)
    postgres.connections[0].terminate()

    with pytest.raises(TelegramRuntimeLockLostError):
        await lock.ensure_healthy()
    with pytest.raises(TelegramRuntimeLockLostError):
        await lock.ensure_healthy()

    assert len(postgres.connections) == 1
    await lock.aclose()
