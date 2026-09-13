"""Typed attendance-resolution outcomes at the service-time boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from friendly_bot.persistence.repositories import (
    AttendanceRecord,
    AttendanceStartResult,
    ServiceBoundSelectionExpiry,
    ServiceRecord,
)
from friendly_bot.services.attendance import ServiceAttendanceService
from friendly_bot.services.lifecycle import ServiceLifecycleService

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
DOORS_OPEN = NOW
DOORS_CLOSE = NOW + timedelta(hours=1)
INTERACTION_END = NOW + timedelta(hours=2)
MINUTE = timedelta(minutes=1)
USER_ID = uuid4()


def service(*, highkey: bool, identifier: UUID | None = None) -> ServiceRecord:
    """Build an ongoing service without borrowing a Zone X seed."""

    return ServiceRecord(
        id=identifier or uuid4(),
        key=f"service-{uuid4().hex}",
        highkey=highkey,
        doors_open_at=DOORS_OPEN,
        doors_close_at=DOORS_CLOSE,
        interaction_ends_at=INTERACTION_END,
    )


class AttendanceUow:
    """F01-shaped test double which exposes only attendance mutations."""

    def __init__(self) -> None:
        self.services = self
        self.attendances = self
        self.locked_user_ids: list[UUID] = []
        self.started: list[tuple[UUID, UUID, str, datetime]] = []
        self._services: dict[UUID, ServiceRecord] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def lock_user(self, user_id: UUID) -> None:
        self.locked_user_ids.append(user_id)

    async def get(self, service_id: UUID) -> ServiceRecord:
        return self._services[service_id]

    async def start_or_switch(
        self,
        user_id: UUID,
        service_id: UUID,
        *,
        attendee_kind: str,
        started_at: datetime,
    ) -> AttendanceStartResult:
        self.started.append((user_id, service_id, attendee_kind, started_at))
        return AttendanceStartResult(
            attendance=AttendanceRecord(
                id=uuid4(),
                service_id=service_id,
                user_id=user_id,
                attendee_kind=attendee_kind,
                started_at=started_at,
                ended_at=None,
            ),
            ended_previous=False,
        )

    def add(self, candidate: ServiceRecord) -> None:
        self._services[candidate.id] = candidate


class LifecycleUow:
    """F01-shaped expiry collaborators for one service lifecycle transaction."""

    def __init__(self, affected_user_ids: frozenset[UUID]) -> None:
        self.open_selections = self
        self.matches = self
        self.attendances = self
        self.affected_user_ids = affected_user_ids
        self.calls: list[tuple[str, UUID, datetime]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def expire_service_bound(
        self, service_id: UUID, *, at: datetime
    ) -> ServiceBoundSelectionExpiry:
        self.calls.append(("expire", service_id, at))
        return ServiceBoundSelectionExpiry(3, self.affected_user_ids)

    async def release_service_bound(self, service_id: UUID, *, at: datetime) -> int:
        self.calls.append(("release", service_id, at))
        return 2

    async def end_active_for_service(
        self, service_id: UUID, *, ended_at: datetime
    ) -> int:
        self.calls.append(("end_attendance", service_id, ended_at))
        return 4


@pytest.mark.parametrize(
    ("services", "now", "expected"),
    [
        ([service(highkey=True)], DOORS_OPEN, "selected"),
        (
            [service(highkey=True), service(highkey=False)],
            DOORS_OPEN,
            "choice_required",
        ),
        ([service(highkey=False)], DOORS_OPEN, "choice_required"),
        ([service(highkey=True)], DOORS_CLOSE + MINUTE, "latecomer"),
        ([service(highkey=True)], INTERACTION_END, "ended"),
    ],
)
async def test_attendance_resolution_returns_only_a_typed_outcome(
    services: list[ServiceRecord], now: datetime, expected: str
) -> None:
    """Rendering copy or treating a stale service as ordinary violates the boundary."""

    attendance = ServiceAttendanceService(lambda: AttendanceUow())

    outcome = await attendance.resolve_for_new_nbnc(USER_ID, services=services, now=now)

    assert outcome.kind == expected


async def test_one_highkey_service_starts_ordinary_attendance_through_f01() -> None:
    """Skipping F01 start-or-switch would lose the atomic overlap guarantee."""

    candidate = service(highkey=True)
    uow = AttendanceUow()
    attendance = ServiceAttendanceService(lambda: uow)

    outcome = await attendance.resolve_for_new_nbnc(
        USER_ID, services=[candidate], now=DOORS_OPEN
    )

    assert outcome.kind == "selected"
    assert uow.started == [(USER_ID, candidate.id, "ordinary", DOORS_OPEN)]
    assert uow.locked_user_ids == [USER_ID]


async def test_no_active_service_returns_typed_none_available() -> None:
    """Rendering a fallback here would bypass the configured no-service flow."""

    attendance = ServiceAttendanceService(lambda: AttendanceUow())

    outcome = await attendance.resolve_for_new_nbnc(USER_ID, services=[], now=NOW)

    assert outcome.kind == "none_available"
    assert outcome.service_id is None
    assert outcome.service_ids == ()


async def test_old_check_in_button_after_doors_close_is_latecomer_not_ordinary() -> (
    None
):
    """A choice rendered before close must re-evaluate service time when clicked."""

    candidate = service(highkey=True)
    uow = AttendanceUow()
    uow.add(candidate)
    attendance = ServiceAttendanceService(lambda: uow)

    outcome = await attendance.select_service(
        USER_ID, candidate.id, now=DOORS_CLOSE + MINUTE
    )

    assert outcome.kind == "latecomer"
    assert uow.started == []


async def test_old_check_in_button_at_interaction_end_creates_no_attendance() -> None:
    """The inclusive interaction-end boundary must prevent a stale enrollment."""

    candidate = service(highkey=True)
    uow = AttendanceUow()
    uow.add(candidate)
    attendance = ServiceAttendanceService(lambda: uow)

    outcome = await attendance.select_service(
        USER_ID, candidate.id, now=INTERACTION_END
    )

    assert outcome.kind == "ended"
    assert uow.started == []


async def test_lifecycle_expires_only_ended_services_and_returns_affected_users() -> (
    None
):
    """Running expiry on an ongoing service would end unrelated work and capacity."""

    expired = service(highkey=True)
    ongoing = ServiceRecord(
        id=uuid4(),
        key="ongoing",
        highkey=True,
        doors_open_at=DOORS_OPEN,
        doors_close_at=DOORS_CLOSE,
        interaction_ends_at=INTERACTION_END + timedelta(hours=1),
    )
    affected_user_ids = frozenset({USER_ID, uuid4()})
    uow = LifecycleUow(affected_user_ids)
    lifecycle = ServiceLifecycleService(lambda: uow)

    outcome = await lifecycle.end_interactions([expired, ongoing], now=INTERACTION_END)

    assert outcome.kind == "ended"
    assert outcome.ended_service_ids == frozenset({expired.id})
    assert outcome.affected_user_ids == affected_user_ids
    assert outcome.expired_selection_count == 3
    assert outcome.released_match_count == 2
    assert outcome.ended_attendance_count == 4
    assert uow.calls == [
        ("expire", expired.id, INTERACTION_END),
        ("release", expired.id, INTERACTION_END),
        ("end_attendance", expired.id, INTERACTION_END),
    ]
