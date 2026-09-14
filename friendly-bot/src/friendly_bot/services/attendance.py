"""Attendance decisions that evaluate service time at the mutation boundary."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from friendly_bot.persistence.repositories import (
    ServiceInteractionClosedError,
    ServiceRecord,
)
from friendly_bot.persistence.uow import UnitOfWork, UnitOfWorkFactory

type AttendanceOutcomeKind = Literal[
    "selected",
    "choice_required",
    "latecomer",
    "none_available",
    "ended",
]


@dataclass(frozen=True, slots=True)
class AttendanceOutcome:
    """A configured-flow decision without user-facing copy or direct dispatch."""

    kind: AttendanceOutcomeKind
    service_id: UUID | None = None
    service_ids: tuple[UUID, ...] = ()


class ServiceAttendanceService:
    """Resolve NBNC attendance through F01's locked attendance repository."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def resolve_for_new_nbnc(
        self,
        user_id: UUID,
        *,
        services: Sequence[ServiceRecord],
        now: datetime,
    ) -> AttendanceOutcome:
        """Select a safe attendance outcome from the services current at this click."""

        async with self._uow_factory() as uow:
            return await self.resolve_for_new_nbnc_in_uow(
                uow, user_id=user_id, services=services, now=now
            )

    async def resolve_for_new_nbnc_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        services: Sequence[ServiceRecord],
        now: datetime,
    ) -> AttendanceOutcome:
        """Resolve attendance under a caller-owned ingress transaction."""

        active_services = tuple(
            service
            for service in services
            if service.doors_open_at <= now < service.interaction_ends_at
        )
        if not active_services:
            if services and all(
                now >= service.interaction_ends_at for service in services
            ):
                return AttendanceOutcome("ended", services[0].id)
            return AttendanceOutcome("none_available")
        if len(active_services) != 1:
            return AttendanceOutcome(
                "choice_required",
                service_ids=tuple(service.id for service in active_services),
            )
        service = active_services[0]
        if not service.highkey:
            return AttendanceOutcome("choice_required", service_ids=(service.id,))
        return await self._resolve_selected_service_in_uow(
            uow, user_id=user_id, service=service, now=now
        )

    async def select_service(
        self, user_id: UUID, service_id: UUID, *, now: datetime
    ) -> AttendanceOutcome:
        """Re-evaluate a previously rendered choice immediately before mutation."""

        async with self._uow_factory() as uow:
            return await self.select_service_in_uow(
                uow, user_id=user_id, service_id=service_id, now=now
            )

    async def select_service_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        service_id: UUID,
        now: datetime,
    ) -> AttendanceOutcome:
        """Re-evaluate and mutate a selected service in a supplied transaction."""

        service = await uow.services.get(service_id)
        return await self._resolve_selected_service_in_uow(
            uow, user_id=user_id, service=service, now=now
        )

    async def _resolve_selected_service_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        service: ServiceRecord,
        now: datetime,
    ) -> AttendanceOutcome:
        if now < service.doors_open_at:
            return AttendanceOutcome("none_available")
        if now >= service.interaction_ends_at:
            return AttendanceOutcome("ended", service.id)
        if now >= service.doors_close_at:
            return AttendanceOutcome("latecomer", service.id)
        await uow.lock_user(user_id)
        try:
            await uow.attendances.start_or_switch(
                user_id,
                service.id,
                attendee_kind="ordinary",
                started_at=now,
            )
        except ServiceInteractionClosedError:
            return AttendanceOutcome("ended", service.id)
        return AttendanceOutcome("selected", service.id)
