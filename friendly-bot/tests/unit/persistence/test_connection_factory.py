"""Contracts for the F01-owned direct PostgreSQL connection factory."""

from __future__ import annotations

import pytest
from pydantic import PostgresDsn

from friendly_bot.persistence.connection import DirectPostgresConnectionFactory


async def test_factory_opens_a_fresh_direct_asyncpg_connection_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pooled or reused connection could release a runtime advisory lock unexpectedly."""

    calls: list[dict[str, object]] = []
    connections = [object(), object()]

    async def connect(**kwargs: object) -> object:
        calls.append(kwargs)
        return connections.pop(0)

    monkeypatch.setattr("friendly_bot.persistence.connection.asyncpg.connect", connect)
    factory = DirectPostgresConnectionFactory(
        PostgresDsn("postgresql+asyncpg://friendly_bot@db.test:5544/friendly_bot")
    )

    first = await factory()
    second = await factory()

    assert first is not second
    assert calls == [
        {
            "host": "db.test",
            "port": 5544,
            "user": "friendly_bot",
            "password": None,
            "database": "friendly_bot",
        },
        {
            "host": "db.test",
            "port": 5544,
            "user": "friendly_bot",
            "password": None,
            "database": "friendly_bot",
        },
    ]
