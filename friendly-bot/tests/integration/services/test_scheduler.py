"""PostgreSQL coverage for the scheduler's authoritative audience adapter."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import Enum, MetaData, select, update
from sqlalchemy.dialects.postgresql import CreateEnumType, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.domain.state import OpenSelectionState
from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import (
    FlowScopeKind,
    OpenFlowSelection,
    OperationalRole,
    OutboundDelivery,
    Service,
    ServiceAttendance,
    ServiceAudience,
    ServiceTimestamp,
    TimestampDeliveryClaim,
    User,
)
from friendly_bot.persistence.repositories import (
    NewOutboundDelivery,
    ServiceTimestampRecord,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.services.scheduler import AudienceResolver, ServiceDeliveryScheduler

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
_SESSION_FACTORY = async_sessionmaker[AsyncSession]


async def _create_schema(
    connection: asyncpg.Connection[asyncpg.Record], schema: str
) -> None:
    """Install the real F01 schema into a disposable PostgreSQL namespace."""

    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata, schema=schema)
    enum_types: dict[str, Enum] = {}
    for table in metadata.sorted_tables:
        for column in table.columns:
            if isinstance(column.type, Enum) and column.type.native_enum:
                column.type.schema = schema
                enum_types.setdefault(column.type.name or column.name, column.type)
    postgres = dialect()
    for enum_type in enum_types.values():
        await connection.execute(
            str(CreateEnumType(enum_type).compile(dialect=postgres))
        )
    for table in metadata.sorted_tables:
        await connection.execute(str(CreateTable(table).compile(dialect=postgres)))
    for table in metadata.sorted_tables:
        for index in table.indexes:
            await connection.execute(str(CreateIndex(index).compile(dialect=postgres)))


@pytest.fixture
async def session_factory() -> AsyncIterator[_SESSION_FACTORY]:
    """Run against a fresh schema only when a PostgreSQL test URL is configured."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")
    url = make_url(database_url)
    schema = f"friendly_bot_task7_scheduler_{uuid4().hex}"
    connection = await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database=url.database,
    )
    await connection.execute(f'CREATE SCHEMA "{schema}"')
    try:
        await _create_schema(connection, schema)
        engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema}},
        )
        try:
            yield async_sessionmaker(engine, expire_on_commit=False)
        finally:
            await engine.dispose()
    finally:
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await connection.close()


@pytest.fixture
def uow_factory(
    session_factory: _SESSION_FACTORY,
) -> Callable[[], UnitOfWork]:
    """Create a real F01 UoW for each audience-resolution read."""

    return lambda: UnitOfWork(session_factory)


@dataclass
class RecordingTimestampRoots:
    """Record only the exact I04 preparer inputs that T02 supplies."""

    calls: list[
        tuple[UnitOfWork, ServiceTimestampRecord, UUID, datetime, NewOutboundDelivery]
    ] = field(default_factory=list)

    async def open_for_recipient(
        self,
        uow: UnitOfWork,
        timestamp: ServiceTimestampRecord,
        user_id: UUID,
        *,
        now: datetime,
    ) -> NewOutboundDelivery:
        delivery = NewOutboundDelivery(
            idempotency_key=f"task8:timestamp:{timestamp.id}:{user_id}",
            user_id=user_id,
            telegram_chat_id=73,
            kind="message",
            payload={"text": "Service timestamp"},
            eligible_at=now,
        )
        self.calls.append((uow, timestamp, user_id, now, delivery))
        return delivery


class PersistingTimestampRoots:
    """Exercise the real F01 root mutation inside T02's scheduler transaction."""

    async def open_for_recipient(
        self,
        uow: UnitOfWork,
        timestamp: ServiceTimestampRecord,
        user_id: UUID,
        *,
        now: datetime,
    ) -> NewOutboundDelivery:
        await uow.lock_user(user_id)
        await uow.open_selections.open_root(
            OpenSelectionState(
                id=uuid4(),
                user_id=user_id,
                flow_version_id=timestamp.flow_version_id,
                parent_flow_key=timestamp.root_flow_key,
                service_id=timestamp.service_id,
                is_current=True,
                is_global_interruptive=False,
                ancestor_flow_keys=(timestamp.root_flow_key,),
                checkpoint_flow_keys=(timestamp.root_flow_key,),
                opened_at=now,
                last_focused_at=now,
            ),
            at=now,
        )
        return NewOutboundDelivery(
            idempotency_key=f"task8:timestamp:{timestamp.id}:{user_id}",
            user_id=user_id,
            telegram_chat_id=73,
            kind="message",
            payload={"text": "Service timestamp"},
            eligible_at=now,
        )


@dataclass
class RecordingUnitOfWorkFactory:
    """Expose the exact scheduler UoWs passed through the preparer port."""

    session_factory: _SESSION_FACTORY
    opened: list[UnitOfWork] = field(default_factory=list)

    def __call__(self) -> UnitOfWork:
        unit_of_work = UnitOfWork(self.session_factory)
        self.opened.append(unit_of_work)
        return unit_of_work


@dataclass
class ClosingAudience:
    """Close the service after due listing to exercise the final claim fence."""

    session_factory: _SESSION_FACTORY
    service_id: UUID
    user_id: UUID
    calls: list[tuple[ServiceAudience, UUID | None]] = field(default_factory=list)

    async def resolve(
        self, audience: ServiceAudience, service_id: UUID | None
    ) -> list[UUID]:
        self.calls.append((audience, service_id))
        async with self.session_factory.begin() as session:
            await session.execute(
                update(Service)
                .where(Service.id == self.service_id)
                .values(interaction_closed_at=NOW)
            )
        return [self.user_id]


async def test_authoritative_audiences_apply_role_inheritance_without_admin_grants(
    session_factory: _SESSION_FACTORY, uow_factory: Callable[[], UnitOfWork]
) -> None:
    """A role-only query or an admin shortcut would return the wrong recipients."""

    service_id = uuid4()
    nbnc_id = uuid4()
    server_id = uuid4()
    leader_id = uuid4()
    staff_id = uuid4()
    admin_only_id = uuid4()
    async with session_factory.begin() as session:
        session.add(
            Service(
                id=service_id,
                key="task7-audience",
                name="Task 7 audience",
                timezone="UTC",
                highkey=True,
                doors_open_at=NOW - timedelta(hours=1),
                doors_close_at=NOW + timedelta(hours=1),
                service_starts_at=NOW,
                service_ends_at=NOW + timedelta(hours=1),
                interaction_ends_at=NOW + timedelta(hours=2),
            )
        )
        session.add_all(
            [
                User(id=nbnc_id, role=OperationalRole.NBNC),
                User(id=server_id, role=OperationalRole.SERVER),
                User(id=leader_id, role=OperationalRole.LEADER),
                User(id=staff_id, role=OperationalRole.STAFF),
                User(
                    id=admin_only_id,
                    role=OperationalRole.NBNC,
                    is_admin=True,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ServiceAttendance(
                    id=uuid4(),
                    service_id=service_id,
                    user_id=user_id,
                    attendee_kind="ordinary",
                    started_at=NOW,
                )
                for user_id in (nbnc_id, server_id, leader_id, staff_id)
            ]
        )

    resolver = AudienceResolver(uow_factory, clock=lambda: NOW)

    assert set(await resolver.resolve(ServiceAudience.ALL_NBNCS, None)) == {
        nbnc_id,
        admin_only_id,
    }
    assert set(await resolver.resolve(ServiceAudience.ALL_SERVERS, None)) == {
        server_id,
        leader_id,
        staff_id,
    }
    assert set(await resolver.resolve(ServiceAudience.ALL_LEADERS, None)) == {
        leader_id,
        staff_id,
    }
    assert set(await resolver.resolve(ServiceAudience.SERVICE_NBNCS, service_id)) == {
        nbnc_id
    }
    assert set(await resolver.resolve(ServiceAudience.SERVICE_SERVERS, service_id)) == {
        server_id,
        leader_id,
        staff_id,
    }
    assert set(await resolver.resolve(ServiceAudience.SERVICE_LEADERS, service_id)) == {
        leader_id,
        staff_id,
    }
    assert set(
        await resolver.resolve(ServiceAudience.ALL_SERVICE_ATTENDEES, service_id)
    ) == {nbnc_id, server_id, leader_id, staff_id}


async def test_restarted_scheduler_claims_and_enqueues_timestamp_through_preparer_once(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """The second scheduler loses the durable claim and cannot invoke I04's port."""

    service_id = uuid4()
    user_id = uuid4()
    timestamp_id = uuid4()
    timestamp_root = "service.task8.timestamp.service_questions"
    async with session_factory.begin() as session:
        session.add_all(
            [
                Service(
                    id=service_id,
                    key="task8-service-start",
                    name="Task 8 service start",
                    timezone="UTC",
                    highkey=True,
                    doors_open_at=NOW - timedelta(hours=1),
                    doors_close_at=NOW + timedelta(hours=1),
                    service_starts_at=NOW,
                    service_ends_at=NOW + timedelta(hours=1),
                    interaction_ends_at=NOW + timedelta(hours=2),
                ),
                User(id=user_id, telegram_user_id=73, role=OperationalRole.NBNC),
            ]
        )
    async with uow_factory() as uow:
        version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": "service.task8.timestamp"},
                flow_key_index={timestamp_root: ()},
            ),
            scope_kind=FlowScopeKind.SERVICE,
            service_id=service_id,
            published_by_user_id=None,
        )
    async with session_factory.begin() as session:
        session.add(
            ServiceTimestamp(
                id=timestamp_id,
                service_id=service_id,
                key="service-start",
                occurs_at=NOW,
                audience=ServiceAudience.ALL_NBNCS,
                flow_version_id=version.id,
                root_flow_key=timestamp_root,
            )
        )

    roots = RecordingTimestampRoots()
    scheduler_uows = RecordingUnitOfWorkFactory(session_factory)
    scheduler = ServiceDeliveryScheduler(
        scheduler_uows, AudienceResolver(uow_factory, clock=lambda: NOW), roots
    )

    first = await scheduler.run_once(now=NOW)
    assert len(roots.calls) == 1
    (
        prepared_uow,
        prepared_timestamp,
        prepared_user_id,
        prepared_now,
        prepared_delivery,
    ) = roots.calls[0]

    async with session_factory() as session:
        deliveries = list(
            await session.scalars(
                select(OutboundDelivery).where(
                    OutboundDelivery.idempotency_key
                    == f"task8:timestamp:{timestamp_id}:{user_id}"
                )
            )
        )

    restarted = ServiceDeliveryScheduler(
        scheduler_uows, AudienceResolver(uow_factory, clock=lambda: NOW), roots
    )
    second = await restarted.run_once(now=NOW)

    assert first.claimed_delivery_count == 1
    assert first.enqueued_delivery_count == 1
    assert second.claimed_delivery_count == 0
    assert second.enqueued_delivery_count == 0
    assert prepared_uow is scheduler_uows.opened[1]
    assert prepared_timestamp.id == timestamp_id
    assert prepared_timestamp.service_id == service_id
    assert prepared_timestamp.root_flow_key == timestamp_root
    assert prepared_user_id == user_id
    assert prepared_now == NOW
    assert prepared_delivery == NewOutboundDelivery(
        idempotency_key=f"task8:timestamp:{timestamp_id}:{user_id}",
        user_id=user_id,
        telegram_chat_id=73,
        kind="message",
        payload={"text": "Service timestamp"},
        eligible_at=NOW,
    )
    assert len(roots.calls) == 1
    assert len(deliveries) == 1
    assert deliveries[0].status == "pending"
    assert deliveries[0].user_id == prepared_delivery.user_id
    assert deliveries[0].telegram_chat_id == prepared_delivery.telegram_chat_id
    assert deliveries[0].kind == prepared_delivery.kind
    assert deliveries[0].payload == prepared_delivery.payload
    assert deliveries[0].eligible_at == prepared_delivery.eligible_at


async def test_timestamp_root_preserves_a_current_onboarding_branch(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Timestamp work must open an independent current branch for the recipient."""

    service_id = uuid4()
    user_id = uuid4()
    timestamp_id = uuid4()
    timestamp_root = "service.task8.timestamp.service_questions"
    onboarding_root = "system.onboarding.name_capture"
    async with session_factory.begin() as session:
        session.add_all(
            [
                Service(
                    id=service_id,
                    key="task8-independent-root",
                    name="Task 8 independent root",
                    timezone="UTC",
                    highkey=True,
                    doors_open_at=NOW - timedelta(hours=1),
                    doors_close_at=NOW + timedelta(hours=1),
                    service_starts_at=NOW,
                    service_ends_at=NOW + timedelta(hours=1),
                    interaction_ends_at=NOW + timedelta(hours=2),
                ),
                User(id=user_id, telegram_user_id=73, role=OperationalRole.NBNC),
            ]
        )
    async with uow_factory() as uow:
        onboarding_version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": onboarding_root},
                flow_key_index={onboarding_root: ()},
            ),
            scope_kind=FlowScopeKind.SYSTEM,
            service_id=None,
            published_by_user_id=None,
        )
        timestamp_version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": timestamp_root},
                flow_key_index={timestamp_root: ()},
            ),
            scope_kind=FlowScopeKind.SERVICE,
            service_id=service_id,
            published_by_user_id=None,
        )
    async with session_factory.begin() as session:
        session.add_all(
            [
                OpenFlowSelection(
                    id=uuid4(),
                    user_id=user_id,
                    flow_version_id=onboarding_version.id,
                    parent_flow_key=onboarding_root,
                    service_id=None,
                    is_current=True,
                    is_global_interruptive=False,
                    ancestor_flow_keys=[onboarding_root],
                    checkpoint_flow_keys=[onboarding_root],
                    opened_at=NOW,
                    last_focused_at=NOW,
                ),
                ServiceTimestamp(
                    id=timestamp_id,
                    service_id=service_id,
                    key="service-start",
                    occurs_at=NOW,
                    audience=ServiceAudience.ALL_NBNCS,
                    flow_version_id=timestamp_version.id,
                    root_flow_key=timestamp_root,
                ),
            ]
        )

    scheduler = ServiceDeliveryScheduler(
        uow_factory,
        AudienceResolver(uow_factory, clock=lambda: NOW),
        PersistingTimestampRoots(),
    )

    result = await scheduler.run_once(now=NOW)
    async with uow_factory() as uow:
        selections = await uow.open_selections.list_for_user(user_id, now=NOW)

    assert result.claimed_delivery_count == 1
    assert result.enqueued_delivery_count == 1
    assert {
        (selection.parent_flow_key, selection.is_current) for selection in selections
    } == {(onboarding_root, True), (timestamp_root, True)}


async def test_closed_service_after_due_listing_cannot_claim_prepare_or_enqueue_timestamp(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """The final claim must reject a closure that races the earlier due query."""

    service_id = uuid4()
    user_id = uuid4()
    timestamp_id = uuid4()
    root_flow_key = "service.task7.timestamp.closed"
    async with session_factory.begin() as session:
        session.add_all(
            [
                Service(
                    id=service_id,
                    key="task7-closed-claim",
                    name="Task 7 closed claim",
                    timezone="UTC",
                    highkey=True,
                    doors_open_at=NOW - timedelta(hours=1),
                    doors_close_at=NOW + timedelta(hours=1),
                    service_starts_at=NOW,
                    service_ends_at=NOW + timedelta(hours=1),
                    interaction_ends_at=NOW + timedelta(hours=2),
                ),
                User(id=user_id, telegram_user_id=74, role=OperationalRole.NBNC),
            ]
        )
    async with uow_factory() as uow:
        version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": "service.task7.timestamp.closed"},
                flow_key_index={root_flow_key: ()},
            ),
            scope_kind=FlowScopeKind.SERVICE,
            service_id=service_id,
            published_by_user_id=None,
        )
    async with session_factory.begin() as session:
        session.add(
            ServiceTimestamp(
                id=timestamp_id,
                service_id=service_id,
                key="closed",
                occurs_at=NOW,
                audience=ServiceAudience.ALL_NBNCS,
                flow_version_id=version.id,
                root_flow_key=root_flow_key,
            )
        )

    roots = RecordingTimestampRoots()
    scheduler = ServiceDeliveryScheduler(
        RecordingUnitOfWorkFactory(session_factory),
        ClosingAudience(session_factory, service_id, user_id),
        roots,
    )

    result = await scheduler.run_once(now=NOW)

    async with session_factory() as session:
        claims = list(
            await session.scalars(
                select(TimestampDeliveryClaim).where(
                    TimestampDeliveryClaim.service_timestamp_id == timestamp_id
                )
            )
        )
        deliveries = list(
            await session.scalars(
                select(OutboundDelivery).where(OutboundDelivery.user_id == user_id)
            )
        )

    assert result.due_timestamp_count == 1
    assert result.claimed_delivery_count == 0
    assert result.enqueued_delivery_count == 0
    assert roots.calls == []
    assert claims == []
    assert deliveries == []
