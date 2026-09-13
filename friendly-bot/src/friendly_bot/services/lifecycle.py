"""Atomic expiry of service-bound selections, matching capacity, and attendance."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from friendly_bot.persistence.repositories import ServiceRecord
from friendly_bot.persistence.uow import UnitOfWorkFactory

type LifecycleOutcomeKind = Literal["ended"]


@dataclass(frozen=True, slots=True)
class ServiceLifecycleOutcome:
    """Durable expiry facts for I04 to perform the configured checkpoint return."""

    kind: LifecycleOutcomeKind
    ended_service_ids: frozenset[UUID]
    affected_user_ids: frozenset[UUID]
    expired_selection_count: int
    released_match_count: int
    ended_attendance_count: int


class ServiceLifecycleService:
    """End only due service interactions through F01's public transaction boundary."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def end_interactions(
        self, services: Sequence[ServiceRecord], *, now: datetime
    ) -> ServiceLifecycleOutcome:
        """Expire each due service atomically and return users for I04 checkpoint work."""

        ended_services = tuple(
            service for service in services if now >= service.interaction_ends_at
        )
        affected_user_ids = frozenset[UUID]()
        expired_selection_count = 0
        released_match_count = 0
        ended_attendance_count = 0
        async with self._uow_factory() as uow:
            for service in ended_services:
                expiry = await uow.open_selections.expire_service_bound(
                    service.id, at=now
                )
                affected_user_ids = affected_user_ids | expiry.affected_user_ids
                expired_selection_count += expiry.expired_selection_count
                released_match_count += await uow.matches.release_service_bound(
                    service.id, at=now
                )
                ended_attendance_count += await uow.attendances.end_active_for_service(
                    service.id, ended_at=now
                )
        return ServiceLifecycleOutcome(
            kind="ended",
            ended_service_ids=frozenset(service.id for service in ended_services),
            affected_user_ids=affected_user_ids,
            expired_selection_count=expired_selection_count,
            released_match_count=released_match_count,
            ended_attendance_count=ended_attendance_count,
        )
