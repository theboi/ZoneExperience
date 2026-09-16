"""Timestamp delivery orchestration over F01 durable scheduling boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from friendly_bot.persistence.models import ServiceAudience
from friendly_bot.persistence.repositories import (
    ServiceInteractionClosedError,
    ServiceTimestampRecord,
)
from friendly_bot.persistence.uow import UnitOfWork, UnitOfWorkFactory
from friendly_bot.telegram.presentations import TelegramPresentation
from friendly_bot.telegram.sender import DirectSendResult, TelegramPresentationSender


class AudienceResolution(Protocol):
    """Resolve the durable audience selected by one timestamp."""

    async def resolve(
        self, audience: ServiceAudience, service_id: UUID | None
    ) -> list[UUID]:
        """Return the authoritative recipient IDs for one configured audience."""


class TimestampRootPreparer(Protocol):
    """I04-owned composition of one timestamp root and all of its durable output."""

    async def open_for_recipient(
        self,
        uow: UnitOfWork,
        timestamp: ServiceTimestampRecord,
        user_id: UUID,
        *,
        now: datetime,
    ) -> TimestampRootPreparation:
        """Open the root and return its post-commit presentations in the supplied UoW."""


@dataclass(frozen=True, slots=True)
class TimestampRootPreparation:
    """Expose I04's in-memory output only until the caller commits its claim."""

    presentations: tuple[TelegramPresentation, ...]


@dataclass(frozen=True, slots=True)
class SchedulerRunResult:
    """Durable work facts from one scheduler tick without retaining payloads."""

    due_timestamp_count: int
    claimed_delivery_count: int
    send_attempted: int
    send_failed: int


class AudienceResolver:
    """Use the F01 service repository as the sole audience authority."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or _utc_now

    async def resolve(
        self, audience: ServiceAudience, service_id: UUID | None
    ) -> list[UUID]:
        """Resolve all seven stored audiences without local role interpretation."""

        async with self._uow_factory() as uow:
            return await uow.services.list_audience_user_ids(
                audience, service_id, now=self._clock()
            )


class ServiceDeliveryScheduler:
    """Claim timestamp recipients before I04 prepares and sends post-commit output."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        audiences: AudienceResolution,
        timestamp_roots: TimestampRootPreparer,
        sender: TelegramPresentationSender | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audiences = audiences
        self._timestamp_roots = timestamp_roots
        self._sender = sender

    async def run_once(self, now: datetime) -> SchedulerRunResult:
        """Schedule every legal due timestamp, including overdue restart catch-up work."""

        async with self._uow_factory() as timestamp_uow:
            due_timestamps = await timestamp_uow.services.list_due_timestamps(now=now)

        claimed_delivery_count = 0
        send_attempted = 0
        send_failed = 0
        for timestamp in due_timestamps:
            recipient_ids = await self._audiences.resolve(
                timestamp.audience, timestamp.service_id
            )
            for user_id in recipient_ids:
                prepared: TimestampRootPreparation | None = None
                async with self._uow_factory() as uow:
                    try:
                        claimed = await uow.services.claim_timestamp_delivery(
                            timestamp.id, user_id, now=now
                        )
                    except ServiceInteractionClosedError:
                        continue
                    if not claimed:
                        continue
                    claimed_delivery_count += 1
                    prepared = await self._timestamp_roots.open_for_recipient(
                        uow, timestamp, user_id, now=now
                    )
                assert prepared is not None
                outcome = await self._send_after_commit(prepared.presentations)
                send_attempted += outcome.attempted
                send_failed += outcome.failed

        return SchedulerRunResult(
            due_timestamp_count=len(due_timestamps),
            claimed_delivery_count=claimed_delivery_count,
            send_attempted=send_attempted,
            send_failed=send_failed,
        )

    async def _send_after_commit(
        self, presentations: tuple[TelegramPresentation, ...]
    ) -> DirectSendResult:
        if self._sender is None or not presentations:
            return DirectSendResult(0, 0, 0)
        return await self._sender.send_all(presentations)


def _utc_now() -> datetime:
    """Read an aware UTC time for repository-scoped audience eligibility."""

    return datetime.now(UTC)
