"""Scheduler orchestration coverage at the F01 and timestamp-preparer boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from friendly_bot.persistence.models import ServiceAudience
from friendly_bot.persistence.repositories import (
    NewOutboundDelivery,
    ServiceInteractionClosedError,
    ServiceTimestampRecord,
)
from friendly_bot.services.scheduler import (
    AudienceResolver,
    ServiceDeliveryScheduler,
    TimestampRootPreparation,
)

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
SERVICE_ID = uuid4()
TIMESTAMP_ID = uuid4()
NBNC = uuid4()
LEADER = uuid4()
STAFF = uuid4()
ADMIN_ONLY = uuid4()


def timestamp(
    *, occurs_at: datetime = NOW - timedelta(minutes=1)
) -> ServiceTimestampRecord:
    """Build one due timestamp without relying on the future Zone X seed."""

    return ServiceTimestampRecord(
        id=TIMESTAMP_ID,
        service_id=SERVICE_ID,
        key="unit.notice",
        occurs_at=occurs_at,
        audience=ServiceAudience.ALL_NBNCS,
        flow_version_id=uuid4(),
        root_flow_key="service.unit.timestamp.notice",
    )


@dataclass
class FakeServices:
    """F01-shaped timestamp and audience reads backed by hand-written facts."""

    due_timestamps: list[ServiceTimestampRecord] = field(default_factory=list)
    audience_user_ids: dict[tuple[ServiceAudience, UUID | None], list[UUID]] = field(
        default_factory=dict
    )
    due_calls: list[datetime] = field(default_factory=list)
    audience_calls: list[tuple[ServiceAudience, UUID | None, datetime]] = field(
        default_factory=list
    )

    async def list_due_timestamps(
        self, *, now: datetime
    ) -> list[ServiceTimestampRecord]:
        self.due_calls.append(now)
        return list(self.due_timestamps)

    async def list_audience_user_ids(
        self, audience: ServiceAudience, service_id: UUID | None, *, now: datetime
    ) -> list[UUID]:
        self.audience_calls.append((audience, service_id, now))
        return list(self.audience_user_ids.get((audience, service_id), []))


@dataclass
class FakeDeliveries:
    """Share the unique timestamp claim across independently opened UoWs."""

    claimed_pairs: set[tuple[UUID, UUID]] = field(default_factory=set)
    successful_claims: list[tuple[UUID, UUID]] = field(default_factory=list)
    enqueued: list[NewOutboundDelivery] = field(default_factory=list)
    close_timestamp_claims: bool = False

    async def claim_timestamp_delivery(
        self, timestamp_id: UUID, user_id: UUID, *, now: datetime
    ) -> bool:
        if self.close_timestamp_claims:
            raise ServiceInteractionClosedError(SERVICE_ID)
        await asyncio.sleep(0)
        pair = (timestamp_id, user_id)
        if pair in self.claimed_pairs:
            return False
        self.claimed_pairs.add(pair)
        self.successful_claims.append(pair)
        return True

    async def enqueue(self, delivery: NewOutboundDelivery) -> NewOutboundDelivery:
        self.enqueued.append(delivery)
        return delivery


class FakeUow:
    """Expose only the scheduler collaborators in one commit-shaped context."""

    def __init__(self, services: FakeServices, deliveries: FakeDeliveries) -> None:
        self.services = services
        self.deliveries = deliveries

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None


@dataclass
class RecordingTimestampRoots:
    """I04-shaped port that preserves a pre-existing independent current branch."""

    current_parent_keys: dict[UUID, set[str]] = field(default_factory=dict)
    calls: list[tuple[object, ServiceTimestampRecord, UUID, datetime]] = field(
        default_factory=list
    )

    async def open_for_recipient(
        self,
        uow: FakeUow,
        timestamp: ServiceTimestampRecord,
        user_id: UUID,
        *,
        now: datetime,
    ) -> TimestampRootPreparation:
        self.calls.append((uow, timestamp, user_id, now))
        self.current_parent_keys.setdefault(user_id, set()).add(timestamp.root_flow_key)
        await uow.deliveries.enqueue(
            NewOutboundDelivery(
                idempotency_key=f"timestamp:{timestamp.id}:{user_id}",
                user_id=user_id,
                telegram_chat_id=900_001,
                kind="message",
                payload={"text": "Timestamp notice"},
                eligible_at=now,
            )
        )
        return TimestampRootPreparation(enqueued_delivery_count=1)


def scheduler_fixture(
    *,
    due_timestamps: list[ServiceTimestampRecord],
    audience_user_ids: dict[tuple[ServiceAudience, UUID | None], list[UUID]],
    roots: RecordingTimestampRoots | None = None,
) -> tuple[
    ServiceDeliveryScheduler, FakeServices, FakeDeliveries, RecordingTimestampRoots
]:
    """Compose the scheduler over shared fake durable state."""

    services = FakeServices(
        due_timestamps=due_timestamps, audience_user_ids=audience_user_ids
    )
    deliveries = FakeDeliveries()
    resolver = AudienceResolver(
        lambda: FakeUow(services, deliveries), clock=lambda: NOW
    )
    timestamp_roots = roots or RecordingTimestampRoots()
    return (
        ServiceDeliveryScheduler(
            lambda: FakeUow(services, deliveries), resolver, timestamp_roots
        ),
        services,
        deliveries,
        timestamp_roots,
    )


@pytest.mark.parametrize(
    ("audience", "service_id", "expected"),
    [
        (ServiceAudience.ALL_NBNCS, None, [NBNC]),
        (ServiceAudience.ALL_SERVERS, None, [LEADER, STAFF]),
        (ServiceAudience.ALL_LEADERS, None, [LEADER, STAFF]),
        (ServiceAudience.SERVICE_NBNCS, SERVICE_ID, [NBNC]),
        (ServiceAudience.SERVICE_SERVERS, SERVICE_ID, [LEADER, STAFF]),
        (ServiceAudience.SERVICE_LEADERS, SERVICE_ID, [LEADER, STAFF]),
        (ServiceAudience.ALL_SERVICE_ATTENDEES, SERVICE_ID, [NBNC, LEADER, STAFF]),
    ],
)
async def test_audience_resolver_preserves_authoritative_membership_for_each_audience(
    audience: ServiceAudience, service_id: UUID | None, expected: list[UUID]
) -> None:
    """Changing the requested audience or dropping members loses authoritative scope."""

    services = FakeServices(audience_user_ids={(audience, service_id): expected})
    resolver = AudienceResolver(
        lambda: FakeUow(services, FakeDeliveries()), clock=lambda: NOW
    )

    assert await resolver.resolve(audience, service_id) == expected
    assert services.audience_calls == [(audience, service_id, NOW)]


async def test_leader_audience_includes_staff_but_not_admin_only() -> None:
    """Replacing the authoritative role hierarchy with an admin check is a bug."""

    services = FakeServices(
        audience_user_ids={(ServiceAudience.ALL_LEADERS, None): [LEADER, STAFF]}
    )
    resolver = AudienceResolver(
        lambda: FakeUow(services, FakeDeliveries()), clock=lambda: NOW
    )

    recipients = await resolver.resolve(ServiceAudience.ALL_LEADERS, None)

    assert recipients == [LEADER, STAFF]
    assert ADMIN_ONLY not in recipients


async def test_scheduler_catches_up_an_overdue_timestamp() -> None:
    """Filtering timestamps to the exact tick would lose restart catch-up work."""

    overdue = timestamp(occurs_at=NOW - timedelta(hours=1))
    scheduler, services, deliveries, roots = scheduler_fixture(
        due_timestamps=[overdue],
        audience_user_ids={(ServiceAudience.ALL_NBNCS, SERVICE_ID): [NBNC]},
    )

    result = await scheduler.run_once(now=NOW)

    assert result.due_timestamp_count == 1
    assert result.enqueued_delivery_count == 1
    assert services.due_calls == [NOW]
    assert deliveries.successful_claims == [(TIMESTAMP_ID, NBNC)]
    assert [delivery.idempotency_key for delivery in deliveries.enqueued] == [
        f"timestamp:{TIMESTAMP_ID}:{NBNC}"
    ]
    assert roots.current_parent_keys[NBNC] == {"service.unit.timestamp.notice"}


async def test_scheduler_skips_timestamp_excluded_at_interaction_end() -> None:
    """Scheduling a timestamp omitted by F01's end-boundary query is illegal work."""

    scheduler, services, deliveries, roots = scheduler_fixture(
        due_timestamps=[],
        audience_user_ids={(ServiceAudience.ALL_NBNCS, SERVICE_ID): [NBNC]},
    )

    result = await scheduler.run_once(now=NOW)

    assert result.due_timestamp_count == 0
    assert result.enqueued_delivery_count == 0
    assert services.audience_calls == []
    assert deliveries.successful_claims == []
    assert roots.calls == []


async def test_scheduler_treats_a_final_closed_timestamp_claim_as_no_work() -> None:
    """A closure after due listing must not prepare a root or enqueue a delivery."""

    scheduler, _, deliveries, roots = scheduler_fixture(
        due_timestamps=[timestamp()],
        audience_user_ids={(ServiceAudience.ALL_NBNCS, SERVICE_ID): [NBNC]},
    )
    deliveries.close_timestamp_claims = True

    result = await scheduler.run_once(now=NOW)

    assert result.claimed_delivery_count == 0
    assert result.enqueued_delivery_count == 0
    assert deliveries.successful_claims == []
    assert roots.calls == []


async def test_concurrent_scheduler_runs_create_one_claim_root_and_delivery() -> None:
    """Moving the claim after root preparation would create duplicate user work."""

    scheduler, _, deliveries, roots = scheduler_fixture(
        due_timestamps=[timestamp()],
        audience_user_ids={(ServiceAudience.ALL_NBNCS, SERVICE_ID): [NBNC]},
    )

    results = await asyncio.gather(
        scheduler.run_once(now=NOW), scheduler.run_once(now=NOW)
    )

    assert sum(result.enqueued_delivery_count for result in results) == 1
    assert deliveries.successful_claims == [(TIMESTAMP_ID, NBNC)]
    assert len(roots.calls) == 1
    assert len(deliveries.enqueued) == 1


async def test_timestamp_preparer_opens_an_independent_current_branch() -> None:
    """Reusing the onboarding branch would close a live prompt during delivery."""

    roots = RecordingTimestampRoots(
        current_parent_keys={NBNC: {"onboarding.name_capture"}}
    )
    scheduler, _, deliveries, prepared_roots = scheduler_fixture(
        due_timestamps=[timestamp()],
        audience_user_ids={(ServiceAudience.ALL_NBNCS, SERVICE_ID): [NBNC]},
        roots=roots,
    )

    await scheduler.run_once(now=NOW)

    assert prepared_roots.current_parent_keys[NBNC] == {
        "onboarding.name_capture",
        "service.unit.timestamp.notice",
    }
    assert len(prepared_roots.calls) == 1
    _, received_timestamp, received_user_id, received_now = prepared_roots.calls[0]
    assert received_timestamp.root_flow_key == "service.unit.timestamp.notice"
    assert received_user_id == NBNC
    assert received_now == NOW
    assert len(deliveries.enqueued) == 1
