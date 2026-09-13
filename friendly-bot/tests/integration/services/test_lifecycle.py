"""PostgreSQL lifecycle coverage for service-bound expiry work."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import Enum, MetaData, select
from sqlalchemy.dialects.postgresql import CreateEnumType, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import (
    CapacityReservation,
    ConversationMessage,
    FlowScopeKind,
    HumanMatchAssignment,
    HumanMatchRequest,
    OpenFlowSelection,
    OperationalProfile,
    OperationalRole,
    Service,
    ServiceAttendance,
    User,
)
from friendly_bot.persistence.repositories import ServiceRecord
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.services.lifecycle import ServiceLifecycleService

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
_SESSION_FACTORY = async_sessionmaker[AsyncSession]


async def _create_schema(
    connection: asyncpg.Connection[asyncpg.Record], schema: str
) -> None:
    """Create the actual F01 metadata in an isolated PostgreSQL schema."""

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
    """Use a fresh generated schema when PostgreSQL integration is configured."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")
    url = make_url(database_url)
    schema = f"friendly_bot_task6_lifecycle_{uuid4().hex}"
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
    """Open one real F01 transaction for each lifecycle service call."""

    return lambda: UnitOfWork(session_factory)


def _service_record(service: Service) -> ServiceRecord:
    return ServiceRecord(
        id=service.id,
        key=service.key,
        highkey=service.highkey,
        doors_open_at=service.doors_open_at,
        doors_close_at=service.doors_close_at,
        interaction_ends_at=service.interaction_ends_at,
    )


async def test_lifecycle_ends_only_expired_service_work_and_retains_history(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A global expiry would corrupt live work; deleting history would erase audit data."""

    expired_service = Service(
        id=uuid4(),
        key="task6-expired",
        name="Expired",
        timezone="UTC",
        highkey=True,
        doors_open_at=NOW - timedelta(hours=2),
        doors_close_at=NOW - timedelta(hours=1),
        service_starts_at=NOW - timedelta(hours=2),
        service_ends_at=NOW - timedelta(hours=1),
        interaction_ends_at=NOW,
    )
    ongoing_service = Service(
        id=uuid4(),
        key="task6-ongoing",
        name="Ongoing",
        timezone="UTC",
        highkey=True,
        doors_open_at=NOW - timedelta(hours=1),
        doors_close_at=NOW + timedelta(hours=1),
        service_starts_at=NOW,
        service_ends_at=NOW + timedelta(hours=2),
        interaction_ends_at=NOW + timedelta(hours=3),
    )
    affected_user_id = uuid4()
    unrelated_user_id = uuid4()
    target_responder_user_id = uuid4()
    unrelated_responder_user_id = uuid4()
    target_profile_id = uuid4()
    unrelated_profile_id = uuid4()
    target_request_id = uuid4()
    unrelated_request_id = uuid4()
    target_reservation_id = uuid4()
    unrelated_reservation_id = uuid4()
    target_assignment_id = uuid4()
    unrelated_assignment_id = uuid4()
    async with session_factory.begin() as session:
        session.add_all(
            [
                expired_service,
                ongoing_service,
                User(id=affected_user_id, role=OperationalRole.NBNC),
                User(id=unrelated_user_id, role=OperationalRole.NBNC),
                User(id=target_responder_user_id, role=OperationalRole.SERVER),
                User(id=unrelated_responder_user_id, role=OperationalRole.SERVER),
                OperationalProfile(
                    id=target_profile_id,
                    user_id=target_responder_user_id,
                    normalized_name="task6-target",
                    dob=NOW.date(),
                    interests=["music"],
                    cg_name=None,
                    capacity=1,
                    reserved_capacity=1,
                ),
                OperationalProfile(
                    id=unrelated_profile_id,
                    user_id=unrelated_responder_user_id,
                    normalized_name="task6-unrelated",
                    dob=NOW.date(),
                    interests=["music"],
                    cg_name=None,
                    capacity=1,
                    reserved_capacity=1,
                ),
                ServiceAttendance(
                    id=uuid4(),
                    service_id=expired_service.id,
                    user_id=affected_user_id,
                    attendee_kind="ordinary",
                    started_at=NOW - timedelta(hours=1),
                ),
                ServiceAttendance(
                    id=uuid4(),
                    service_id=ongoing_service.id,
                    user_id=unrelated_user_id,
                    attendee_kind="ordinary",
                    started_at=NOW - timedelta(minutes=30),
                ),
                HumanMatchRequest(
                    id=target_request_id,
                    requester_user_id=affected_user_id,
                    service_id=expired_service.id,
                    kind="normal",
                    status="pending",
                    created_at=NOW - timedelta(minutes=30),
                ),
                HumanMatchRequest(
                    id=unrelated_request_id,
                    requester_user_id=unrelated_user_id,
                    service_id=ongoing_service.id,
                    kind="normal",
                    status="pending",
                    created_at=NOW - timedelta(minutes=30),
                ),
                CapacityReservation(
                    id=target_reservation_id,
                    operational_profile_id=target_profile_id,
                    request_id=target_request_id,
                    reserved_at=NOW - timedelta(minutes=30),
                ),
                CapacityReservation(
                    id=unrelated_reservation_id,
                    operational_profile_id=unrelated_profile_id,
                    request_id=unrelated_request_id,
                    reserved_at=NOW - timedelta(minutes=30),
                ),
                HumanMatchAssignment(
                    id=target_assignment_id,
                    request_id=target_request_id,
                    responder_profile_id=target_profile_id,
                    capacity_reservation_id=target_reservation_id,
                    assigned_at=NOW - timedelta(minutes=30),
                ),
                HumanMatchAssignment(
                    id=unrelated_assignment_id,
                    request_id=unrelated_request_id,
                    responder_profile_id=unrelated_profile_id,
                    capacity_reservation_id=unrelated_reservation_id,
                    assigned_at=NOW - timedelta(minutes=30),
                ),
                ConversationMessage(
                    id=uuid4(),
                    user_id=affected_user_id,
                    source_kind="telegram",
                    source_message_id=17,
                    body="keep this history",
                    replied_to_body=None,
                    occurred_at=NOW - timedelta(minutes=20),
                ),
            ]
        )
    async with uow_factory() as uow:
        expired_version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": "service.expired"},
                flow_key_index={"service.expired": ()},
            ),
            scope_kind=FlowScopeKind.SERVICE,
            service_id=expired_service.id,
            published_by_user_id=None,
        )
        ongoing_version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": "service.ongoing"},
                flow_key_index={"service.ongoing": ()},
            ),
            scope_kind=FlowScopeKind.SERVICE,
            service_id=ongoing_service.id,
            published_by_user_id=None,
        )
    expired_selection_id = uuid4()
    ongoing_selection_id = uuid4()
    async with session_factory.begin() as session:
        session.add_all(
            [
                OpenFlowSelection(
                    id=expired_selection_id,
                    user_id=affected_user_id,
                    flow_version_id=expired_version.id,
                    parent_flow_key="service.expired.choice",
                    service_id=expired_service.id,
                    is_current=True,
                    is_global_interruptive=False,
                    ancestor_flow_keys=["service.expired", "service.expired.choice"],
                    checkpoint_flow_keys=["service.expired"],
                    opened_at=NOW - timedelta(minutes=30),
                    last_focused_at=NOW - timedelta(minutes=30),
                ),
                OpenFlowSelection(
                    id=ongoing_selection_id,
                    user_id=unrelated_user_id,
                    flow_version_id=ongoing_version.id,
                    parent_flow_key="service.ongoing.choice",
                    service_id=ongoing_service.id,
                    is_current=True,
                    is_global_interruptive=False,
                    ancestor_flow_keys=["service.ongoing", "service.ongoing.choice"],
                    checkpoint_flow_keys=["service.ongoing"],
                    opened_at=NOW - timedelta(minutes=30),
                    last_focused_at=NOW - timedelta(minutes=30),
                ),
            ]
        )

    outcome = await ServiceLifecycleService(uow_factory).end_interactions(
        [_service_record(expired_service), _service_record(ongoing_service)], now=NOW
    )

    async with session_factory() as session:
        expired_attendance = await session.scalar(
            select(ServiceAttendance).where(
                ServiceAttendance.service_id == expired_service.id
            )
        )
        ongoing_attendance = await session.scalar(
            select(ServiceAttendance).where(
                ServiceAttendance.service_id == ongoing_service.id
            )
        )
        target_assignment = await session.get(
            HumanMatchAssignment, target_assignment_id
        )
        unrelated_assignment = await session.get(
            HumanMatchAssignment, unrelated_assignment_id
        )
        target_reservation = await session.get(
            CapacityReservation, target_reservation_id
        )
        unrelated_reservation = await session.get(
            CapacityReservation, unrelated_reservation_id
        )
        target_profile = await session.get(OperationalProfile, target_profile_id)
        unrelated_profile = await session.get(OperationalProfile, unrelated_profile_id)
        expired_selection = await session.get(OpenFlowSelection, expired_selection_id)
        ongoing_selection = await session.get(OpenFlowSelection, ongoing_selection_id)
        history = list(
            await session.scalars(
                select(ConversationMessage).where(
                    ConversationMessage.user_id == affected_user_id
                )
            )
        )

    assert outcome.kind == "ended"
    assert outcome.ended_service_ids == frozenset({expired_service.id})
    assert outcome.affected_user_ids == frozenset({affected_user_id})
    assert outcome.expired_selection_count == 1
    assert outcome.released_match_count == 1
    assert outcome.ended_attendance_count == 1
    assert expired_attendance is not None
    assert expired_attendance.ended_at == NOW
    assert ongoing_attendance is not None
    assert ongoing_attendance.ended_at is None
    assert target_assignment is not None
    assert target_assignment.released_at == NOW
    assert target_assignment.release_reason == "service_interaction_ended"
    assert unrelated_assignment is not None
    assert unrelated_assignment.released_at is None
    assert target_reservation is not None
    assert target_reservation.released_at == NOW
    assert unrelated_reservation is not None
    assert unrelated_reservation.released_at is None
    assert target_profile is not None
    assert target_profile.reserved_capacity == 0
    assert unrelated_profile is not None
    assert unrelated_profile.reserved_capacity == 1
    assert expired_selection is not None
    assert expired_selection.expires_at == NOW
    assert ongoing_selection is not None
    assert ongoing_selection.expires_at is None
    assert [message.body for message in history] == ["keep this history"]
