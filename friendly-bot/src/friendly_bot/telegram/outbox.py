"""Durable, no-replay Telegram outbound-delivery worker."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Literal, Protocol, Self
from uuid import UUID, uuid4

import httpx

from friendly_bot.persistence.repositories import (
    DeliveryAttemptRecord,
    DeliveryClaimLostError,
    DeliveryOutcome,
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

type DeliveryDisposition = Literal["sent", "retry", "rejected", "uncertain"]

_RETRY_BACKOFF_SECONDS = (5, 30, 120)


class TelegramDeliveryGateway(Protocol):
    """The direct Telegram send boundary needed by durable delivery."""

    async def send(self, request: OutboundTelegramMessage) -> TelegramSendOutcome:
        """Attempt exactly one typed Telegram message delivery."""


class TelegramDeliveryOperations(Protocol):
    """The exact durable delivery operations consumed by this worker."""

    async def claim_next_safe(self, *, now: datetime) -> OutboundDeliveryRecord | None:
        """Commit a safe durable delivery claim, if one is available."""

    async def start_attempt(
        self,
        delivery_id: UUID,
        correlation_id: UUID,
        *,
        claim_token: UUID,
        started_at: datetime,
    ) -> DeliveryAttemptRecord:
        """Commit a token-fenced send attempt before the network boundary."""

    async def finish_attempt(
        self,
        delivery_id: UUID,
        attempt_id: UUID,
        outcome: DeliveryOutcome,
        *,
        now: datetime,
        retry_at: datetime | None = None,
        safe_error: str | None = None,
        confirmed_telegram_message_id: int | None = None,
    ) -> None:
        """Persist a terminal or retryable attempt result."""

    async def extend_telegram_pause(self, *, pause_until: datetime) -> datetime:
        """Extend the durable account-wide Telegram rate-limit pause."""


class TelegramDeliveryUnitOfWork(Protocol):
    """The transaction shape required for one outbox delivery phase."""

    @property
    def deliveries(self) -> TelegramDeliveryOperations:
        """Expose only the delivery repository methods this worker needs."""

    async def __aenter__(self) -> Self:
        """Enter the transaction boundary."""

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit on success or roll back on an exception."""


class TelegramDeliveryUnitOfWorkFactory(Protocol):
    """Create a fresh delivery transaction for each worker phase."""

    def __call__(self) -> TelegramDeliveryUnitOfWork:
        """Return an unopened transaction boundary."""


@dataclass(frozen=True, slots=True)
class _DeliveryResolution:
    disposition: DeliveryDisposition
    safe_error: str | None
    retry_at: datetime | None = None
    pause_until: datetime | None = None
    confirmed_telegram_message_id: int | None = None


class OutboundDeliveryWorker:
    """Claim durable work, commit an attempt, send once, and safely finalize it."""

    def __init__(
        self,
        unit_of_work_factory: TelegramDeliveryUnitOfWorkFactory,
        gateway: TelegramDeliveryGateway,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._gateway = gateway
        self._clock = clock or _utc_now

    async def run_once(self) -> bool:
        """Process at most one safe delivery without replaying ambiguous sends."""

        async with self._unit_of_work_factory() as claim_uow:
            claim = await claim_uow.deliveries.claim_next_safe(now=self._clock())
        if claim is None or claim.claim_token is None:
            return False

        try:
            async with self._unit_of_work_factory() as start_uow:
                attempt = await start_uow.deliveries.start_attempt(
                    claim.id,
                    uuid4(),
                    claim_token=claim.claim_token,
                    started_at=self._clock(),
                )
        except DeliveryClaimLostError:
            return False

        request = _telegram_request_from(claim.message, claim.idempotency_key)
        if request is None:
            await self._finish(
                claim.id,
                attempt,
                _DeliveryResolution("rejected", "telegram_message_unsupported"),
            )
            return True

        try:
            outcome = await self._gateway.send(request)
        except asyncio.CancelledError:
            await self._finish_after_cancellation(
                claim.id,
                attempt,
                _DeliveryResolution("uncertain", "telegram_send_cancelled"),
            )
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            await self._finish(
                claim.id,
                attempt,
                _DeliveryResolution("uncertain", "telegram_transport_error"),
            )
            return True

        await self._finish(
            claim.id,
            attempt,
            _classify_outcome(outcome, attempt.attempt_number, self._clock()),
        )
        return True

    async def _finish(
        self,
        delivery_id: UUID,
        attempt: DeliveryAttemptRecord,
        resolution: _DeliveryResolution,
    ) -> None:
        """Commit the typed final state in its own transaction after the send."""

        async with self._unit_of_work_factory() as finish_uow:
            await finish_uow.deliveries.finish_attempt(
                delivery_id,
                attempt.id,
                resolution.disposition,
                now=self._clock(),
                retry_at=resolution.retry_at,
                safe_error=resolution.safe_error,
                confirmed_telegram_message_id=resolution.confirmed_telegram_message_id,
            )
            if resolution.pause_until is not None:
                await finish_uow.deliveries.extend_telegram_pause(
                    pause_until=resolution.pause_until
                )

    async def _finish_after_cancellation(
        self,
        delivery_id: UUID,
        attempt: DeliveryAttemptRecord,
        resolution: _DeliveryResolution,
    ) -> None:
        """Keep uncertainty finalization alive if shutdown cancels us again."""

        finalization = asyncio.create_task(
            self._finish(delivery_id, attempt, resolution)
        )
        try:
            await asyncio.shield(finalization)
        except asyncio.CancelledError:
            finalization.add_done_callback(_consume_background_result)
            raise


def _telegram_request_from(
    message: OutboundDeliveryMessage, idempotency_key: str
) -> OutboundTelegramMessage | None:
    """Map an immutable provider-neutral F01 message only after its claim commits."""

    if (
        message.kind != "message"
        or set(message.payload) != {"text"}
        or type(message.chat_id) is not int
        or message.chat_id <= 0
        or type(idempotency_key) is not str
        or not idempotency_key
    ):
        return None
    text = message.payload.get("text")
    if type(text) is not str or not text:
        return None
    return OutboundTelegramMessage(
        chat_id=message.chat_id,
        text=text,
        idempotency_key=idempotency_key,
    )


def _classify_outcome(
    outcome: TelegramSendOutcome,
    attempt_number: int,
    now: datetime,
) -> _DeliveryResolution:
    """Turn only strict direct-gateway outcomes into durable safe dispositions."""

    if isinstance(outcome, TelegramSendConfirmed):
        return _DeliveryResolution(
            "sent",
            None,
            confirmed_telegram_message_id=outcome.message_id,
        )
    if isinstance(outcome, TelegramSendRejected):
        return _DeliveryResolution("rejected", "telegram_rejected")
    if isinstance(outcome, TelegramResponseUncertain):
        return _DeliveryResolution("uncertain", outcome.code)
    if isinstance(outcome, TelegramSendRetry):
        if outcome.error_code == 429:
            if outcome.retry_after_seconds is None:
                return _DeliveryResolution("uncertain", "telegram_response_malformed")
            pause_until = now + timedelta(seconds=outcome.retry_after_seconds)
            return replace(
                _retry_or_reject(
                    attempt_number,
                    pause_until,
                    "telegram_rate_limited",
                ),
                pause_until=pause_until,
            )
        if 500 <= outcome.error_code <= 599:
            backoff_index = min(attempt_number - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
            return _retry_or_reject(
                attempt_number,
                now + timedelta(seconds=_RETRY_BACKOFF_SECONDS[backoff_index]),
                "telegram_temporary_error",
            )
    return _DeliveryResolution("uncertain", "telegram_response_malformed")


def _retry_or_reject(
    attempt_number: int, retry_at: datetime, safe_error: str
) -> _DeliveryResolution:
    """Bound automatic retries so a definite failure cannot retry forever."""

    if attempt_number >= len(_RETRY_BACKOFF_SECONDS):
        return _DeliveryResolution("rejected", "telegram_retry_limit_exhausted")
    return _DeliveryResolution("retry", safe_error, retry_at=retry_at)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _consume_background_result(finalization: asyncio.Task[None]) -> None:
    """Observe a detached finalization failure without retaining a task warning."""

    if not finalization.cancelled():
        finalization.exception()
