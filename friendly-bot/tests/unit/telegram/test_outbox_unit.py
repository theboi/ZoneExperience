"""Unit contracts for the durable Telegram outbound-delivery worker."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType, TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from friendly_bot.persistence.repositories import (
    DeliveryAttemptRecord,
    DeliveryClaimLostError,
    OutboundDeliveryMessage,
    OutboundDeliveryRecord,
)
from friendly_bot.telegram.models import (
    OutboundTelegramMessage,
    TelegramResponseUncertain,
    TelegramSendConfirmed,
    TelegramSendOutcome,
    TelegramSendRejected,
    TelegramSendRetry,
)
from friendly_bot.telegram.outbox import OutboundDeliveryWorker

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


@dataclass
class RecordingGateway:
    """A typed gateway double that observes only immutable mapped requests."""

    outcome: TelegramSendOutcome | BaseException
    requests: list[OutboundTelegramMessage] = field(default_factory=list)
    requires_started_commit: Callable[[], bool] | None = None

    async def send(self, request: OutboundTelegramMessage) -> TelegramSendOutcome:
        if self.requires_started_commit is not None:
            assert self.requires_started_commit()
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@dataclass
class RecordingDeliveries:
    """The exact F01 delivery calls the worker is permitted to make."""

    claim: OutboundDeliveryRecord | None
    attempt_number: int = 1
    start_error: DeliveryClaimLostError | None = None
    finish_calls: list[dict[str, object]] = field(default_factory=list)
    claimed_at: list[datetime] = field(default_factory=list)
    started: bool = False

    async def claim_next_safe(self, *, now: datetime) -> OutboundDeliveryRecord | None:
        self.claimed_at.append(now)
        return self.claim

    async def start_attempt(
        self,
        delivery_id: UUID,
        correlation_id: UUID,
        *,
        claim_token: UUID,
        started_at: datetime,
    ) -> DeliveryAttemptRecord:
        assert self.claim is not None
        assert delivery_id == self.claim.id
        assert claim_token == self.claim.claim_token
        if self.start_error is not None:
            raise self.start_error
        self.started = True
        return DeliveryAttemptRecord(
            id=uuid4(),
            delivery_id=delivery_id,
            attempt_number=self.attempt_number,
            started_at=started_at,
            correlation_id=correlation_id,
        )

    async def finish_attempt(
        self,
        delivery_id: UUID,
        attempt_id: UUID,
        outcome: str,
        *,
        now: datetime,
        retry_at: datetime | None = None,
        safe_error: str | None = None,
        confirmed_telegram_message_id: int | None = None,
    ) -> None:
        self.finish_calls.append(
            {
                "delivery_id": delivery_id,
                "attempt_id": attempt_id,
                "outcome": outcome,
                "now": now,
                "retry_at": retry_at,
                "safe_error": safe_error,
                "confirmed_telegram_message_id": confirmed_telegram_message_id,
            }
        )


@dataclass
class RecordingUnitOfWork:
    """A transaction double that records a successful commit on context exit."""

    label: str
    deliveries: RecordingDeliveries
    commits: list[str]

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.commits.append(self.label)


class RecordingUnitOfWorkFactory:
    """Create the claim, start, and finish transactions in that order."""

    def __init__(self, deliveries: RecordingDeliveries) -> None:
        self.deliveries = deliveries
        self.commits: list[str] = []
        self._labels = iter(("claim", "start", "finish"))

    def __call__(self) -> RecordingUnitOfWork:
        return RecordingUnitOfWork(next(self._labels), self.deliveries, self.commits)


def _claim() -> OutboundDeliveryRecord:
    return OutboundDeliveryRecord(
        id=uuid4(),
        idempotency_key="outbox:welcome:73",
        status="claimed",
        message=OutboundDeliveryMessage(
            chat_id=73,
            kind="message",
            payload=MappingProxyType({"text": "Welcome"}),
        ),
        eligible_at=NOW,
        claim_token=uuid4(),
        claim_expires_at=NOW + timedelta(minutes=1),
        confirmed_telegram_message_id=None,
    )


async def test_commits_claim_and_started_attempt_before_exactly_one_send() -> None:
    """Sending before either commit could duplicate a delivery after a crash."""

    deliveries = RecordingDeliveries(_claim())
    factory = RecordingUnitOfWorkFactory(deliveries)
    gateway = RecordingGateway(
        TelegramSendConfirmed(901),
        requires_started_commit=lambda: factory.commits == ["claim", "start"],
    )

    assert await OutboundDeliveryWorker(factory, gateway, clock=lambda: NOW).run_once()

    assert gateway.requests == [
        OutboundTelegramMessage(73, "Welcome", "outbox:welcome:73")
    ]
    assert factory.commits == ["claim", "start", "finish"]
    finish = deliveries.finish_calls[0]
    assert deliveries.claim is not None
    assert finish["delivery_id"] == deliveries.claim.id
    assert finish["outcome"] == "sent"
    assert finish["now"] == NOW
    assert finish["retry_at"] is None
    assert finish["safe_error"] is None
    assert finish["confirmed_telegram_message_id"] == 901


@pytest.mark.parametrize(
    ("telegram_outcome", "expected", "retry_at", "safe_error"),
    [
        (
            TelegramSendRetry(503),
            "retry",
            NOW + timedelta(seconds=5),
            "telegram_temporary_error",
        ),
        (
            TelegramSendRetry(429, 17),
            "retry",
            NOW + timedelta(seconds=17),
            "telegram_rate_limited",
        ),
        (TelegramSendRejected(403), "rejected", None, "telegram_rejected"),
        (
            TelegramResponseUncertain("telegram_transport_error"),
            "uncertain",
            None,
            "telegram_transport_error",
        ),
    ],
)
async def test_classifies_only_typed_definite_non_sends_as_retryable(
    telegram_outcome: TelegramSendOutcome,
    expected: str,
    retry_at: datetime | None,
    safe_error: str,
) -> None:
    """Replaying an ambiguous Telegram send could deliver a duplicate message."""

    deliveries = RecordingDeliveries(_claim())
    factory = RecordingUnitOfWorkFactory(deliveries)

    assert await OutboundDeliveryWorker(
        factory, RecordingGateway(telegram_outcome), clock=lambda: NOW
    ).run_once()

    finish = deliveries.finish_calls[0]
    assert finish["outcome"] == expected
    assert finish["retry_at"] == retry_at
    assert finish["safe_error"] == safe_error


async def test_lost_claim_does_not_cross_the_telegram_network_boundary() -> None:
    """A stale claimant must not send work that another worker may now own."""

    deliveries = RecordingDeliveries(
        _claim(), start_error=DeliveryClaimLostError("claim was lost")
    )
    factory = RecordingUnitOfWorkFactory(deliveries)
    gateway = RecordingGateway(TelegramSendConfirmed(901))

    assert not await OutboundDeliveryWorker(
        factory, gateway, clock=lambda: NOW
    ).run_once()

    assert gateway.requests == []
    assert deliveries.finish_calls == []
    assert factory.commits == ["claim"]


async def test_retry_limit_makes_a_definite_failure_terminal() -> None:
    """A parsed 5xx must not remain automatically retryable forever."""

    deliveries = RecordingDeliveries(_claim(), attempt_number=3)
    factory = RecordingUnitOfWorkFactory(deliveries)

    assert await OutboundDeliveryWorker(
        factory, RecordingGateway(TelegramSendRetry(503)), clock=lambda: NOW
    ).run_once()

    finish = deliveries.finish_calls[0]
    assert finish["outcome"] == "rejected"
    assert finish["retry_at"] is None
    assert finish["safe_error"] == "telegram_retry_limit_exhausted"


async def test_cancelled_send_is_persisted_uncertain_before_cancellation_reraises() -> (
    None
):
    """Cancellation during a request is ambiguous and must never become retry work."""

    deliveries = RecordingDeliveries(_claim())
    factory = RecordingUnitOfWorkFactory(deliveries)

    with pytest.raises(asyncio.CancelledError):
        await OutboundDeliveryWorker(
            factory,
            RecordingGateway(asyncio.CancelledError()),
            clock=lambda: NOW,
        ).run_once()

    assert factory.commits == ["claim", "start", "finish"]
    assert deliveries.finish_calls[0]["outcome"] == "uncertain"
    assert deliveries.finish_calls[0]["safe_error"] == "telegram_send_cancelled"
