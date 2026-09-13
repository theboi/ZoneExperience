"""Dedicated PostgreSQL session lock for the one Friendly Bot polling runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from types import TracebackType
from typing import Final, Protocol, Self

# The signed 64-bit PostgreSQL advisory-lock key for ASCII ``FRIENDLY``.
# Changing it would allow an old and new Friendly Bot runtime to run together.
TELEGRAM_RUNTIME_ADVISORY_LOCK_KEY: Final = 0x465249454E444C59
_TRY_LOCK_SQL: Final = "SELECT pg_try_advisory_lock($1::bigint)"
_HEALTH_SQL: Final = "SELECT 1"


class TelegramRuntimeAlreadyRunningError(RuntimeError):
    """Another process already owns the Friendly Bot long-polling runtime."""


class TelegramRuntimeLockLostError(RuntimeError):
    """The dedicated PostgreSQL session that held the runtime lock is unavailable."""


class TelegramRuntimeConnection(Protocol):
    """The minimal direct PostgreSQL connection needed for a session advisory lock."""

    async def fetchval(self, query: str, *args: object) -> object:
        """Run a scalar PostgreSQL query on this same dedicated session."""

    async def close(self) -> None:
        """Close the dedicated session and thereby release its session-level lock."""

    def is_closed(self) -> bool:
        """Report whether the dedicated session has been lost or closed."""


type TelegramRuntimeConnectionFactory = Callable[
    [], Awaitable[TelegramRuntimeConnection]
]


class TelegramRuntimeLock:
    """Hold one fixed PostgreSQL advisory lock for the complete polling runtime.

    The injected factory must return a fresh, dedicated, non-pooled PostgreSQL
    connection. Session advisory locks disappear when that connection is lost;
    this object then remains permanently unhealthy and never reacquires it.
    """

    def __init__(self, connection: TelegramRuntimeConnection) -> None:
        self._connection: TelegramRuntimeConnection | None = connection
        self._lost = False

    @classmethod
    async def acquire(
        cls, connection_factory: TelegramRuntimeConnectionFactory
    ) -> Self:
        """Open one dedicated session and acquire the fixed singleton lock once."""

        factory_task = asyncio.ensure_future(connection_factory())
        try:
            connection = await asyncio.shield(factory_task)
        except asyncio.CancelledError:
            cleanup_task = asyncio.create_task(
                _close_factory_result_after_cancellation(factory_task)
            )
            await asyncio.shield(cleanup_task)
            raise
        except Exception as error:
            raise TelegramRuntimeLockLostError(
                "runtime_lock_connection_lost"
            ) from error

        try:
            result = await connection.fetchval(
                _TRY_LOCK_SQL, TELEGRAM_RUNTIME_ADVISORY_LOCK_KEY
            )
        except asyncio.CancelledError:
            with suppress(Exception):
                await _close_after_cancellation(connection)
            raise
        except Exception as error:
            await _close_after_failed_acquisition(connection)
            raise TelegramRuntimeLockLostError(
                "runtime_lock_connection_lost"
            ) from error

        if result is False:
            await _close_after_failed_acquisition(connection)
            raise TelegramRuntimeAlreadyRunningError("telegram_runtime_already_running")
        if result is not True:
            await _close_after_failed_acquisition(connection)
            raise TelegramRuntimeLockLostError("runtime_lock_connection_lost")
        return cls(connection)

    async def __aenter__(self) -> Self:
        try:
            await self.ensure_healthy()
        except BaseException:
            with suppress(Exception):
                await self.aclose()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def ensure_healthy(self) -> None:
        """Raise when this runtime no longer owns a live advisory-lock session."""

        connection = self._required_live_connection()
        if connection.is_closed():
            self._lost = True
            raise TelegramRuntimeLockLostError("runtime_lock_connection_lost")
        try:
            result = await connection.fetchval(_HEALTH_SQL)
        except asyncio.CancelledError:
            self._lost = True
            raise
        except Exception as error:
            self._lost = True
            raise TelegramRuntimeLockLostError(
                "runtime_lock_connection_lost"
            ) from error
        if type(result) is not int or result != 1:
            self._lost = True
            raise TelegramRuntimeLockLostError("runtime_lock_connection_lost")

    async def aclose(self) -> None:
        """Close the dedicated session, releasing its session-level advisory lock."""

        connection = self._connection
        self._connection = None
        self._lost = True
        if connection is None or connection.is_closed():
            return
        try:
            await connection.close()
        except asyncio.CancelledError:
            with suppress(Exception):
                await _close_after_cancellation(connection)
            raise
        except Exception as error:
            raise TelegramRuntimeLockLostError(
                "runtime_lock_connection_lost"
            ) from error

    def _required_live_connection(self) -> TelegramRuntimeConnection:
        if self._lost or self._connection is None:
            raise TelegramRuntimeLockLostError("runtime_lock_connection_lost")
        return self._connection


async def _close_after_failed_acquisition(
    connection: TelegramRuntimeConnection,
) -> None:
    """Release a failed acquisition's session before reporting its safe outcome."""

    if connection.is_closed():
        return
    try:
        await connection.close()
    except asyncio.CancelledError:
        with suppress(Exception):
            await _close_after_cancellation(connection)
        raise


async def _close_after_cancellation(connection: TelegramRuntimeConnection) -> None:
    """Finish connection cleanup despite a second cancellation during shutdown."""

    if connection.is_closed():
        return
    close_task = asyncio.create_task(connection.close())
    try:
        await asyncio.shield(close_task)
    except asyncio.CancelledError:
        await asyncio.shield(close_task)
        raise


async def _close_factory_result_after_cancellation(
    factory_task: asyncio.Future[TelegramRuntimeConnection],
) -> None:
    """Close an eventual factory result after its caller has been cancelled."""

    try:
        with suppress(Exception):
            connection = await asyncio.shield(factory_task)
            await _close_after_cancellation(connection)
    except asyncio.CancelledError:
        return
