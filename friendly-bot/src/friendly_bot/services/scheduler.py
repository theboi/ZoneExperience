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
        """Open the root and enqueue every composed delivery in the supplied UoW."""


@dataclass(frozen=True, slots=True)
class TimestampRootPreparation:
    """Count I04-owned outbox rows without leaking their provider payloads."""

    enqueued_delivery_count: int

    def __post_init__(self) -> None:
        if self.enqueued_delivery_count < 0:
            raise ValueError("timestamp preparation delivery count cannot be negative")


@dataclass(frozen=True, slots=True)
class SchedulerRunResult:
    """Durable work facts from one scheduler tick without retaining payloads."""

    due_timestamp_count: int
    claimed_delivery_count: int
    enqueued_delivery_count: int


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
    """Claim timestamp recipients before I04 prepares roots and durable outbox work."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        audiences: AudienceResolution,
        timestamp_roots: TimestampRootPreparer,
    ) -> None:
        self._uow_factory = uow_factory
        self._audiences = audiences
        self._timestamp_roots = timestamp_roots

    async def run_once(self, now: datetime) -> SchedulerRunResult:
        """Schedule every legal due timestamp, including overdue restart catch-up work."""

        async with self._uow_factory() as timestamp_uow:
            due_timestamps = await timestamp_uow.services.list_due_timestamps(now=now)

        claimed_delivery_count = 0
        enqueued_delivery_count = 0
        for timestamp in due_timestamps:
            recipient_ids = await self._audiences.resolve(
                timestamp.audience, timestamp.service_id
            )
            for user_id in recipient_ids:
                async with self._uow_factory() as uow:
                    try:
                        claimed = await uow.deliveries.claim_timestamp_delivery(
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
                    enqueued_delivery_count += prepared.enqueued_delivery_count

        return SchedulerRunResult(
            due_timestamp_count=len(due_timestamps),
            claimed_delivery_count=claimed_delivery_count,
            enqueued_delivery_count=enqueued_delivery_count,
        )


def _utc_now() -> datetime:
    """Read an aware UTC time for repository-scoped audience eligibility."""

    return datetime.now(UTC)
