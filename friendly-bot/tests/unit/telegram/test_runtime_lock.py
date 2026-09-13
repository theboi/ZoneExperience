"""Cancellation safety contracts for the Telegram runtime session lock."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from friendly_bot.telegram.runtime_lock import (
    TelegramRuntimeLock,
    TelegramRuntimeLockLostError,
)


class FakeConnection:
    """A direct-connection double whose query and close outcomes are explicit."""

    def __init__(
        self,
        query_outcomes: Sequence[object | BaseException],
        close_outcomes: Sequence[BaseException | None] = (),
    ) -> None:
        self._query_outcomes = list(query_outcomes)
        self._close_outcomes = list(close_outcomes)
        self.closed = False
        self.close_calls = 0

    async def fetchval(self, _query: str, *_args: object) -> object:
        outcome = self._query_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.close_calls += 1
        outcome = self._close_outcomes.pop(0) if self._close_outcomes else None
        if outcome is not None:
            raise outcome
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed


async def _factory(connection: FakeConnection) -> FakeConnection:
    return connection


async def test_cancelled_lock_query_closes_its_acquired_session_before_reraising() -> (
    None
):
    """A cancellation after the session opens must not strand an advisory-lock owner."""

    connection = FakeConnection([asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await TelegramRuntimeLock.acquire(lambda: _factory(connection))

    assert connection.closed
    assert connection.close_calls == 1


async def test_cancelled_health_check_latches_the_lock_as_lost() -> None:
    """Continuing after a cancelled ownership probe could run without a known lock."""

    connection = FakeConnection([True, asyncio.CancelledError(), 1])
    lock = await TelegramRuntimeLock.acquire(lambda: _factory(connection))

    with pytest.raises(asyncio.CancelledError):
        await lock.ensure_healthy()

    with pytest.raises(TelegramRuntimeLockLostError):
        await lock.ensure_healthy()


async def test_cancelled_close_finishes_releasing_before_the_lock_becomes_unusable() -> (
    None
):
    """A cancelled shutdown must not leave a reusable object holding its session lock."""

    connection = FakeConnection([True], [asyncio.CancelledError()])
    lock = await TelegramRuntimeLock.acquire(lambda: _factory(connection))

    with pytest.raises(asyncio.CancelledError):
        await lock.aclose()

    assert connection.closed
    assert connection.close_calls == 2
    with pytest.raises(TelegramRuntimeLockLostError):
        await lock.ensure_healthy()
