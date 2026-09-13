"""Direct PostgreSQL connection factories owned by F01 persistence."""

from __future__ import annotations

import asyncpg  # type: ignore[import-untyped]
from pydantic import PostgresDsn
from sqlalchemy.engine import make_url


class DirectPostgresConnectionFactory:
    """Open a fresh, non-pooled PostgreSQL connection for session-owned work."""

    def __init__(self, database_url: PostgresDsn) -> None:
        self._url = make_url(str(database_url))

    async def __call__(self) -> asyncpg.Connection[asyncpg.Record]:
        """Create one direct connection without an ORM session or connection pool."""

        return await asyncpg.connect(
            host=self._url.host,
            port=self._url.port,
            user=self._url.username,
            password=self._url.password,
            database=self._url.database,
        )
