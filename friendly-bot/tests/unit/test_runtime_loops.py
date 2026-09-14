"""Runtime-loop resilience behaviour that cannot be owned by a transport unit test."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

import pytest

from friendly_bot.app import FriendlyBotRuntime, _poll_forever
from friendly_bot.telegram.models import TelegramApiError
from friendly_bot.telegram.poller import PollResult
from friendly_bot.telegram.runtime_lock import TelegramRuntimeLock


@dataclass
class HealthyLock:
    """Keep the runtime alive while the poller reports a typed API failure."""

    health_checks: int = 0

    async def ensure_healthy(self) -> None:
        self.health_checks += 1


@dataclass
class ReturnedFailurePoller:
    """Expose whether the runtime attempts a second poll before its idle wait."""

    first_result_returned: asyncio.Event = field(default_factory=asyncio.Event)
    second_call_started: asyncio.Event = field(default_factory=asyncio.Event)
    calls: int = 0

    async def run_once(self, *, now: datetime) -> PollResult:
        assert now.tzinfo is UTC
        self.calls += 1
        if self.calls == 1:
            self.first_result_returned.set()
        else:
            self.second_call_started.set()
        await asyncio.sleep(0)
        return PollResult(0, (), TelegramApiError(502))


@dataclass
class RuntimeWithPoller:
    """The poll-loop dependency needed by this unit-level runtime test."""

    poller: ReturnedFailurePoller


@pytest.mark.asyncio
async def test_poll_loop_waits_before_retrying_a_returned_telegram_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Removing the safe-failure wait would begin the second poll immediately."""

    stop_event = asyncio.Event()
    lock = HealthyLock()
    poller = ReturnedFailurePoller()
    task = asyncio.create_task(
        _poll_forever(
            cast(FriendlyBotRuntime, RuntimeWithPoller(poller)),
            cast(TelegramRuntimeLock, lock),
            stop_event,
        )
    )
    try:
        await poller.first_result_returned.wait()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(poller.second_call_started.wait(), timeout=0.05)
        assert poller.calls == 1
        assert lock.health_checks == 1
    finally:
        stop_event.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert caplog.messages == ["telegram polling failure: api_error status=502"]
