"""PostgreSQL lifecycle coverage for service-bound expiry work."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import Enum, MetaData, select, text
from sqlalchemy.dialects.postgresql import CreateEnumType, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.domain.state import OpenSelectionState, SelectionTransition
from friendly_bot.matching.service import MatchingService
from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import (
    CapacityReservation,
    ConversationMessage,
    FlowScopeKind,
    HumanMatchAssignment,
    HumanMatchExclusion,
    HumanMatchRequest,
    OpenFlowSelection,
    OperationalProfile,
    OperationalRole,
    Service,
    ServiceAttendance,
    User,
)
from friendly_bot.persistence.repositories import (
    ServiceInteractionClosedError,
    ServiceRecord,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.routing.contracts import MatchRankingRequest
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


class _ClosureBarrierServices:
    """Pause the real lifecycle immediately after its durable closure claim."""

    def __init__(
        self,
        services: object,
        claimed: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self._services = services
        self._claimed = claimed
        self._release = release

    async def claim_interaction_closure(
        self, service_id: UUID, *, now: datetime
    ) -> bool:
        claim = await self._services.claim_interaction_closure(service_id, now=now)
        if claim:
            self._claimed.set()
            await self._release.wait()
        return claim


class _ClosureBarrierUow:
    """Expose the real F01 repositories while retaining the claimed service row lock."""

    def __init__(
        self,
        factory: Callable[[], UnitOfWork],
        claimed: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self._factory = factory
        self._claimed = claimed
        self._release = release
        self._uow: UnitOfWork | None = None

    async def __aenter__(self) -> Self:
        self._uow = self._factory()
        active = await self._uow.__aenter__()
        self.services = _ClosureBarrierServices(
            active.services, self._claimed, self._release
        )
        self.open_selections = active.open_selections
        self.matches = active.matches
        self.attendances = active.attendances
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._uow is None:
            raise RuntimeError("barrier unit of work is not active")
        await self._uow.__aexit__(*args)


class _BackendProbeUow:
    """Expose a real F01 UoW and publish the writer's PostgreSQL backend id."""

    def __init__(
        self,
        factory: Callable[[], UnitOfWork],
        backend_pid: asyncio.Future[int],
    ) -> None:
        self._factory = factory
        self._backend_pid = backend_pid
        self._uow: UnitOfWork | None = None

    async def __aenter__(self) -> Self:
        self._uow = self._factory()
        await self._uow.__aenter__()
        session = self._uow._required_session()
        backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
        if backend_pid is None:
            raise RuntimeError("writer PostgreSQL backend was not available")
        self._backend_pid.set_result(int(backend_pid))
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._uow is None:
            raise RuntimeError("rematch probe unit of work is not active")
        await self._uow.__aexit__(*args)

    async def lock_user(self, user_id: UUID) -> None:
        if self._uow is None:
            raise RuntimeError("rematch probe unit of work is not active")
        await self._uow.lock_user(user_id)

    def __getattr__(self, name: str) -> object:
        if self._uow is None:
            raise RuntimeError("backend probe unit of work is not active")
        return getattr(self._uow, name)


class _FirstCandidateRanker:
    """Make the real R03 rematch choose its first durable eligible candidate."""

    async def rank_aliases(self, request: MatchRankingRequest) -> list[str]:
        return [request.candidates[0].alias]


async def _wait_for_postgres_lock(
    session_factory: _SESSION_FACTORY, backend_pid: int
) -> None:
    """Prove the writer is waiting on the held service row, not scheduling."""

    async with session_factory() as observer:
        for _ in range(100):
            wait_event_type = await observer.scalar(
                text(
                    "SELECT wait_event_type FROM pg_stat_activity "
                    "WHERE pid = :backend_pid"
                ),
                {"backend_pid": backend_pid},
            )
            if wait_event_type == "Lock":
                return
            await asyncio.sleep(0.01)
    raise AssertionError("rematch writer did not wait on the service row lock")


async def _assert_lifecycle_closure_fences_writer(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
    service: Service,
    writer: Callable[[Callable[[], _BackendProbeUow]], Awaitable[None]],
) -> None:
    """Run a real lifecycle transaction that holds the claimed service row as a barrier."""

    claimed = asyncio.Event()
    release = asyncio.Event()
    lifecycle = ServiceLifecycleService(
        lambda: _ClosureBarrierUow(uow_factory, claimed, release)
    )
    lifecycle_task = asyncio.create_task(
        lifecycle.end_interactions([_service_record(service)], now=NOW)
    )
    await asyncio.wait_for(claimed.wait(), timeout=1)
    backend_pid = asyncio.get_running_loop().create_future()
    writer_task = asyncio.create_task(
        writer(lambda: _BackendProbeUow(uow_factory, backend_pid))
    )
    await _wait_for_postgres_lock(
        session_factory,
        await asyncio.wait_for(backend_pid, timeout=1),
    )
    release.set()
    await lifecycle_task
    with pytest.raises(ServiceInteractionClosedError):
        await writer_task


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


async def test_lifecycle_closure_fences_a_waiting_service_match_reservation(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A reservation blocked behind lifecycle closure must roll back without capacity use."""

    service = Service(
        id=uuid4(),
        key="task6-race-match",
        name="Race match",
        timezone="UTC",
        highkey=True,
        doors_open_at=NOW - timedelta(hours=2),
        doors_close_at=NOW - timedelta(hours=1),
        service_starts_at=NOW - timedelta(hours=2),
        service_ends_at=NOW - timedelta(hours=1),
        interaction_ends_at=NOW,
    )
    requester_id = uuid4()
    responder_id = uuid4()
    profile_id = uuid4()
    request_id = uuid4()
    async with session_factory.begin() as session:
        session.add_all(
            [
                service,
                User(id=requester_id, role=OperationalRole.NBNC),
                User(id=responder_id, role=OperationalRole.SERVER),
                OperationalProfile(
                    id=profile_id,
                    user_id=responder_id,
                    normalized_name="task6-race-match",
                    dob=NOW.date(),
                    interests=["music"],
                    cg_name=None,
                    capacity=1,
                    reserved_capacity=0,
                ),
                HumanMatchRequest(
                    id=request_id,
                    requester_user_id=requester_id,
                    service_id=service.id,
                    kind="normal",
                    status="pending",
                    created_at=NOW - timedelta(minutes=1),
                ),
            ]
        )

    async def writer(writer_uow_factory: Callable[[], _BackendProbeUow]) -> None:
        async with writer_uow_factory() as uow:
            await uow.matches.reserve_ranked(request_id, [profile_id], now=NOW)

    await _assert_lifecycle_closure_fences_writer(
        session_factory, uow_factory, service, writer
    )
    async with session_factory() as session:
        profile = await session.get(OperationalProfile, profile_id)
        reservations = list(await session.scalars(select(CapacityReservation)))

    assert profile is not None
    assert profile.reserved_capacity == 0
    assert reservations == []


async def test_lifecycle_closure_fences_an_existing_assignment_rematch(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A rematch must block on the service fence before releasing its assignment."""

    service = Service(
        id=uuid4(),
        key="task6-race-rematch",
        name="Race rematch",
        timezone="UTC",
        highkey=True,
        doors_open_at=NOW - timedelta(hours=2),
        doors_close_at=NOW - timedelta(hours=1),
        service_starts_at=NOW - timedelta(hours=2),
        service_ends_at=NOW - timedelta(hours=1),
        interaction_ends_at=NOW,
    )
    requester_id = uuid4()
    assigned_user_id = uuid4()
    fallback_user_id = uuid4()
    assigned_profile_id = uuid4()
    fallback_profile_id = uuid4()
    request_id = uuid4()
    reservation_id = uuid4()
    assignment_id = uuid4()
    async with session_factory.begin() as session:
        session.add_all(
            [
                service,
                User(id=requester_id, role=OperationalRole.NBNC),
                User(id=assigned_user_id, role=OperationalRole.SERVER),
                User(id=fallback_user_id, role=OperationalRole.SERVER),
                OperationalProfile(
                    id=assigned_profile_id,
                    user_id=assigned_user_id,
                    normalized_name="task6-race-rematch-assigned",
                    dob=NOW.date(),
                    interests=["music"],
                    cg_name=None,
                    capacity=1,
                    reserved_capacity=1,
                ),
                OperationalProfile(
                    id=fallback_profile_id,
                    user_id=fallback_user_id,
                    normalized_name="task6-race-rematch-fallback",
                    dob=NOW.date(),
                    interests=["music"],
                    cg_name=None,
                    capacity=1,
                    reserved_capacity=0,
                ),
                HumanMatchRequest(
                    id=request_id,
                    requester_user_id=requester_id,
                    service_id=service.id,
                    kind="normal",
                    status="pending",
                    created_at=NOW - timedelta(minutes=1),
                ),
                CapacityReservation(
                    id=reservation_id,
                    operational_profile_id=assigned_profile_id,
                    request_id=request_id,
                    reserved_at=NOW - timedelta(minutes=1),
                ),
                HumanMatchAssignment(
                    id=assignment_id,
                    request_id=request_id,
                    responder_profile_id=assigned_profile_id,
                    capacity_reservation_id=reservation_id,
                    assigned_at=NOW - timedelta(minutes=1),
                ),
                ServiceAttendance(
                    id=uuid4(),
                    service_id=service.id,
                    user_id=fallback_user_id,
                    attendee_kind="server",
                    started_at=NOW - timedelta(minutes=1),
                ),
            ]
        )

    claimed = asyncio.Event()
    release = asyncio.Event()
    lifecycle = ServiceLifecycleService(
        lambda: _ClosureBarrierUow(uow_factory, claimed, release)
    )
    lifecycle_task = asyncio.create_task(
        lifecycle.end_interactions([_service_record(service)], now=NOW)
    )
    await asyncio.wait_for(claimed.wait(), timeout=1)

    backend_pid = asyncio.get_running_loop().create_future()
    rematch = MatchingService(
        lambda: _BackendProbeUow(uow_factory, backend_pid),
        _FirstCandidateRanker(),
        lambda _: requester_id,
    )
    rematch_task = asyncio.create_task(
        rematch.rematch_normal(
            request_id,
            assigned_profile_id,
            service.id,
            now=NOW,
        )
    )
    await _wait_for_postgres_lock(
        session_factory, await asyncio.wait_for(backend_pid, timeout=1)
    )

    release.set()
    await asyncio.wait_for(lifecycle_task, timeout=2)
    with pytest.raises(ServiceInteractionClosedError):
        await rematch_task

    async with session_factory() as session:
        assignments = list(
            await session.scalars(
                select(HumanMatchAssignment).where(
                    HumanMatchAssignment.request_id == request_id
                )
            )
        )
        reservations = list(
            await session.scalars(
                select(CapacityReservation).where(
                    CapacityReservation.request_id == request_id
                )
            )
        )
        exclusions = list(
            await session.scalars(
                select(HumanMatchExclusion).where(
                    HumanMatchExclusion.request_id == request_id
                )
            )
        )
        assigned_profile = await session.get(OperationalProfile, assigned_profile_id)
        fallback_profile = await session.get(OperationalProfile, fallback_profile_id)

    assert len(assignments) == 1
    assert assignments[0].id == assignment_id
    assert assignments[0].released_at == NOW
    assert assignments[0].release_reason == "service_interaction_ended"
    assert len(reservations) == 1
    assert reservations[0].id == reservation_id
    assert reservations[0].released_at == NOW
    assert exclusions == []
    assert assigned_profile is not None
    assert assigned_profile.reserved_capacity == 0
    assert fallback_profile is not None
    assert fallback_profile.reserved_capacity == 0


async def test_lifecycle_closure_fences_a_waiting_service_selection_apply(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A source branch must check its service fence before it can mutate selections."""

    service = Service(
        id=uuid4(),
        key="task6-race-selection",
        name="Race selection",
        timezone="UTC",
        highkey=True,
        doors_open_at=NOW - timedelta(hours=2),
        doors_close_at=NOW - timedelta(hours=1),
        service_starts_at=NOW - timedelta(hours=2),
        service_ends_at=NOW - timedelta(hours=1),
        interaction_ends_at=NOW,
    )
    user_id = uuid4()
    async with session_factory.begin() as session:
        session.add_all(
            [
                service,
                User(id=user_id, role=OperationalRole.NBNC),
            ]
        )
    async with uow_factory() as uow:
        version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": "service.race"},
                flow_key_index={"service.race": ()},
            ),
            scope_kind=FlowScopeKind.SERVICE,
            service_id=service.id,
            published_by_user_id=None,
        )
    source = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=version.id,
        parent_flow_key="service.race",
        service_id=service.id,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("service.race",),
        checkpoint_flow_keys=("service.race",),
        opened_at=NOW - timedelta(minutes=1),
        last_focused_at=NOW - timedelta(minutes=1),
    )
    child = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=version.id,
        parent_flow_key="service.race.child",
        service_id=service.id,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("service.race", "service.race.child"),
        checkpoint_flow_keys=("service.race",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    async with session_factory.begin() as session:
        session.add(
            OpenFlowSelection(
                id=source.id,
                user_id=source.user_id,
                flow_version_id=source.flow_version_id,
                parent_flow_key=source.parent_flow_key,
                service_id=source.service_id,
                is_current=source.is_current,
                is_global_interruptive=source.is_global_interruptive,
                ancestor_flow_keys=list(source.ancestor_flow_keys),
                checkpoint_flow_keys=list(source.checkpoint_flow_keys),
                opened_at=source.opened_at,
                last_focused_at=source.last_focused_at,
            )
        )
    transition = SelectionTransition(
        source_selection_id=source.id,
        delete_selection_ids=frozenset(),
        reusable_past_selection_ids=frozenset({source.id}),
        current_selection_ids=frozenset({child.parent_flow_key}),
        upsert_selections=(child,),
        child_actions=(),
        checkpoint_return=None,
        generic_leaf_return_suppressed=False,
    )

    async def writer(writer_uow_factory: Callable[[], _BackendProbeUow]) -> None:
        async with writer_uow_factory() as uow:
            await uow.lock_user(user_id)
            await uow.open_selections.apply(transition, at=NOW)

    await _assert_lifecycle_closure_fences_writer(
        session_factory, uow_factory, service, writer
    )
    async with session_factory() as session:
        selections = list(await session.scalars(select(OpenFlowSelection)))

    assert [selection.id for selection in selections] == [source.id]


async def test_lifecycle_closure_fences_a_waiting_attendance_start(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A writer waiting on the service row cannot create attendance after release."""

    service = Service(
        id=uuid4(),
        key="task6-race-attendance",
        name="Race attendance",
        timezone="UTC",
        highkey=True,
        doors_open_at=NOW - timedelta(hours=2),
        doors_close_at=NOW - timedelta(hours=1),
        service_starts_at=NOW - timedelta(hours=2),
        service_ends_at=NOW - timedelta(hours=1),
        interaction_ends_at=NOW,
    )
    user_id = uuid4()
    async with session_factory.begin() as session:
        session.add_all(
            [
                service,
                User(id=user_id, role=OperationalRole.NBNC),
            ]
        )

    async def writer(writer_uow_factory: Callable[[], _BackendProbeUow]) -> None:
        async with writer_uow_factory() as uow:
            await uow.attendances.start_or_switch(
                user_id,
                service.id,
                attendee_kind="ordinary",
                started_at=NOW - timedelta(minutes=1),
            )

    await _assert_lifecycle_closure_fences_writer(
        session_factory, uow_factory, service, writer
    )
    async with session_factory() as session:
        attendance = await session.scalar(
            select(ServiceAttendance).where(ServiceAttendance.user_id == user_id)
        )

    assert attendance is None
