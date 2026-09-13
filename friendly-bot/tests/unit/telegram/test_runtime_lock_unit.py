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
        self.closed_event = asyncio.Event()
        self.close_calls = 0
        self.close_started: asyncio.Event | None = None
        self.close_release: asyncio.Event | None = None

    async def fetchval(self, _query: str, *_args: object) -> object:
        outcome = self._query_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_started is not None:
            self.close_started.set()
        if self.close_release is not None:
            await self.close_release.wait()
        outcome = self._close_outcomes.pop(0) if self._close_outcomes else None
        if outcome is not None:
            raise outcome
        self.closed = True
        self.closed_event.set()

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


async def test_cancelled_factory_closes_a_connection_that_returns_after_cancellation() -> (
    None
):
    """Cancelling startup must not abandon a session created by a still-running factory."""

    connection = FakeConnection([True])
    factory_started = asyncio.Event()
    allow_factory_result = asyncio.Event()

    async def delayed_factory() -> FakeConnection:
        factory_started.set()
        await allow_factory_result.wait()
        return connection

    acquire_task = asyncio.create_task(TelegramRuntimeLock.acquire(delayed_factory))
    await factory_started.wait()
    acquire_task.cancel()
    await asyncio.sleep(0)
    allow_factory_result.set()

    with pytest.raises(asyncio.CancelledError):
        await acquire_task

    assert connection.closed


async def test_second_cancellation_keeps_factory_result_cleanup_running() -> None:
    """A second startup cancellation must not abandon the factory's late connection."""

    connection = FakeConnection([True])
    factory_started = asyncio.Event()
    allow_factory_result = asyncio.Event()

    async def delayed_factory() -> FakeConnection:
        factory_started.set()
        await allow_factory_result.wait()
        return connection

    acquire_task = asyncio.create_task(TelegramRuntimeLock.acquire(delayed_factory))
    await factory_started.wait()
    acquire_task.cancel()
    await asyncio.sleep(0)
    acquire_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await acquire_task

    allow_factory_result.set()
    await asyncio.wait_for(connection.closed_event.wait(), timeout=1)
    assert connection.closed


async def test_cancelled_rejected_acquisition_finishes_closing_its_session() -> None:
    """Cancellation while rejecting a second holder must not retain its unused session."""

    connection = FakeConnection([False])
    connection.close_started = asyncio.Event()
    connection.close_release = asyncio.Event()

    acquire_task = asyncio.create_task(
        TelegramRuntimeLock.acquire(lambda: _factory(connection))
    )
    await connection.close_started.wait()
    acquire_task.cancel()
    await asyncio.sleep(0)
    connection.close_release.set()

    with pytest.raises(asyncio.CancelledError):
        await acquire_task

    assert connection.closed
    assert connection.close_calls == 2


async def test_failed_context_enter_closes_the_acquired_lock_session() -> None:
    """A health failure during context entry bypasses `__aexit__`, so enter must clean up."""

    connection = FakeConnection([True, RuntimeError("offline")])

    with pytest.raises(TelegramRuntimeLockLostError):
        async with await TelegramRuntimeLock.acquire(lambda: _factory(connection)):
            pytest.fail("the lock context body must not start")

    assert connection.closed
