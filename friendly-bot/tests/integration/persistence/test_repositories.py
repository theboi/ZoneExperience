"""PostgreSQL integration coverage for the F01 repository boundary."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import Enum, MetaData, select
from sqlalchemy.dialects.postgresql import CreateEnumType, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.domain.state import (
    CheckpointReturnTransition,
    OpenSelectionState,
    SelectionTransition,
)
from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import (
    CapacityReservation,
    FlowScopeKind,
    HumanMatchAssignment,
    HumanMatchExclusion,
    HumanMatchRequest,
    OpenFlowSelection,
    OperationalProfile,
    OperationalRole,
    OutboundDelivery,
    OutboundDeliveryAttempt,
    Service,
    ServiceAttendance,
    ServiceAudience,
    ServiceTimestamp,
    TimestampDeliveryClaim,
    User,
)
from friendly_bot.persistence.repositories import (
    DeliveryClaimLostError,
    NewOutboundDelivery,
    ServiceInteractionClosedError,
)
from friendly_bot.persistence.uow import UnitOfWork

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_SESSION_FACTORY = async_sessionmaker[AsyncSession]


async def _create_schema(
    connection: asyncpg.Connection[asyncpg.Record], schema: str
) -> None:
    """Create the shared F01 metadata in a generated, isolated schema."""

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
    """Bind integration sessions to one generated schema and remove it afterward."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")

    url = make_url(database_url)
    schema = f"friendly_bot_task6_repositories_{uuid4().hex}"
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
    """Create independent transactions over the fixture's generated schema."""

    return lambda: UnitOfWork(session_factory)


async def _seed_user(
    session_factory: _SESSION_FACTORY,
    *,
    user_id: UUID | None = None,
    telegram_user_id: int | None = None,
    role: OperationalRole = OperationalRole.NBNC,
    is_admin: bool = False,
) -> UUID:
    user_id = user_id or uuid4()
    async with session_factory.begin() as session:
        session.add(
            User(
                id=user_id,
                telegram_user_id=telegram_user_id,
                role=role,
                is_admin=is_admin,
            )
        )
    return user_id


async def _seed_service(
    session_factory: _SESSION_FACTORY,
    *,
    key: str,
    interaction_ends_at: datetime | None = None,
) -> UUID:
    service_id = uuid4()
    async with session_factory.begin() as session:
        session.add(
            Service(
                id=service_id,
                key=key,
                name=key,
                timezone="Asia/Singapore",
                highkey=True,
                doors_open_at=NOW - timedelta(hours=1),
                doors_close_at=NOW + timedelta(hours=1),
                service_starts_at=NOW,
                service_ends_at=NOW + timedelta(hours=2),
                interaction_ends_at=interaction_ends_at or NOW + timedelta(hours=3),
            )
        )
    return service_id


async def _seed_profile(
    session_factory: _SESSION_FACTORY,
    *,
    user_id: UUID,
    name: str,
    always_available: bool = False,
    capacity: int = 1,
) -> UUID:
    profile_id = uuid4()
    async with session_factory.begin() as session:
        session.add(
            OperationalProfile(
                id=profile_id,
                user_id=user_id,
                normalized_name=name,
                dob=date(1990, 1, 1),
                interests=["welcome"],
                cg_name=name.title(),
                always_available=always_available,
                capacity=capacity,
                reserved_capacity=0,
            )
        )
    return profile_id


async def _seed_request(
    session_factory: _SESSION_FACTORY,
    *,
    requester_user_id: UUID,
    service_id: UUID | None,
) -> UUID:
    request_id = uuid4()
    async with session_factory.begin() as session:
        session.add(
            HumanMatchRequest(
                id=request_id,
                requester_user_id=requester_user_id,
                service_id=service_id,
                kind="normal",
                status="pending",
                created_at=NOW,
            )
        )
    return request_id


async def _seed_selection(
    session_factory: _SESSION_FACTORY, selection: OpenSelectionState
) -> None:
    """Persist a branch source directly so repository transitions can lock it."""

    async with session_factory.begin() as session:
        session.add(
            OpenFlowSelection(
                id=selection.id,
                user_id=selection.user_id,
                flow_version_id=selection.flow_version_id,
                parent_flow_key=selection.parent_flow_key,
                service_id=selection.service_id,
                is_current=selection.is_current,
                is_global_interruptive=selection.is_global_interruptive,
                ancestor_flow_keys=list(selection.ancestor_flow_keys),
                checkpoint_flow_keys=list(selection.checkpoint_flow_keys),
                opened_at=selection.opened_at,
                last_focused_at=selection.last_focused_at,
            )
        )


async def _claim_same_update(factory: Callable[[], UnitOfWork]) -> bool:
    async with factory() as uow:
        return await uow.updates.claim_update(77, received_at=NOW)


async def test_concurrent_update_claim_has_exactly_one_winner(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Removing the conflict-safe insert would permit duplicate update processing."""

    winners = await asyncio.gather(*[_claim_same_update(uow_factory) for _ in range(2)])

    assert winners.count(True) == 1


async def test_user_and_operational_login_records_never_expose_a_profile_role(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A role lookup must come from users while active login occupancy stays exclusive."""

    profile_user_id = await _seed_user(session_factory, role=OperationalRole.LEADER)
    profile_id = await _seed_profile(
        session_factory, user_id=profile_user_id, name="jordan"
    )
    first_user_id = await _seed_user(session_factory, telegram_user_id=101)
    second_user_id = await _seed_user(session_factory, telegram_user_id=202)

    async with uow_factory() as uow:
        first_attachment = await uow.operational_logins.attach(
            profile_id, first_user_id, at=NOW
        )
        occupied_attachment = await uow.operational_logins.attach(
            profile_id, second_user_id, at=NOW
        )
        user = await uow.users.require_by_telegram_id(101)
        profile = await uow.operational_profiles.find_by_login_identity(
            "jordan", date(1990, 1, 1)
        )

    assert first_attachment.kind == "attached"
    assert first_attachment.is_first_ever_attachment is True
    assert occupied_attachment.kind == "occupied"
    assert occupied_attachment.is_first_ever_attachment is False
    assert user.role is OperationalRole.NBNC
    assert profile is not None
    assert not hasattr(profile, "role")


async def test_reattached_profile_reports_noninitial_history(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Deriving first-login state from active occupancy would reopen it after logout."""

    profile_user_id = await _seed_user(session_factory)
    profile_id = await _seed_profile(
        session_factory, user_id=profile_user_id, name="returning"
    )
    telegram_user_id = await _seed_user(session_factory, telegram_user_id=303)

    async with uow_factory() as uow:
        first_attachment = await uow.operational_logins.attach(
            profile_id, telegram_user_id, at=NOW
        )
    async with uow_factory() as uow:
        detached = await uow.operational_logins.detach_for_user(
            telegram_user_id, at=NOW + timedelta(minutes=1)
        )
    async with uow_factory() as uow:
        reattached = await uow.operational_logins.attach(
            profile_id, telegram_user_id, at=NOW + timedelta(minutes=2)
        )

    assert first_attachment.kind == "attached"
    assert first_attachment.is_first_ever_attachment is True
    assert detached is not None
    assert reattached.kind == "attached"
    assert reattached.is_first_ever_attachment is False


async def test_attendance_switch_ends_previous_active_service(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A switch must preserve old history while leaving exactly one active attendance."""

    user_id = await _seed_user(session_factory)
    first_service_id = await _seed_service(session_factory, key="zone_x_first")
    second_service_id = await _seed_service(session_factory, key="zone_x_second")

    async with uow_factory() as uow:
        first = await uow.attendances.start_or_switch(
            user_id, first_service_id, attendee_kind="ordinary", started_at=NOW
        )
    async with uow_factory() as uow:
        second = await uow.attendances.start_or_switch(
            user_id,
            second_service_id,
            attendee_kind="latecomer",
            started_at=NOW + timedelta(minutes=5),
        )
        active = await uow.attendances.active_for_user(
            user_id, now=NOW + timedelta(minutes=5)
        )

    assert first.ended_previous is False
    assert second.ended_previous is True
    assert active is not None
    assert active.id == second.attendance.id
    assert active.attendee_kind == "latecomer"


async def test_end_active_for_service_preserves_a_previously_switched_history_time(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Lifecycle cleanup must not overwrite an attendance already ended by a switch."""

    user_id = await _seed_user(session_factory)
    first_service_id = await _seed_service(session_factory, key="task6-history-first")
    second_service_id = await _seed_service(session_factory, key="task6-history-second")
    switched_at = NOW + timedelta(minutes=1)

    async with uow_factory() as uow:
        await uow.attendances.start_or_switch(
            user_id, first_service_id, attendee_kind="ordinary", started_at=NOW
        )
        await uow.attendances.start_or_switch(
            user_id,
            second_service_id,
            attendee_kind="ordinary",
            started_at=switched_at,
        )
    async with uow_factory() as uow:
        ended = await uow.attendances.end_active_for_service(
            first_service_id, ended_at=NOW + timedelta(minutes=2)
        )
    async with session_factory() as session:
        history = await session.scalar(
            select(ServiceAttendance).where(
                ServiceAttendance.user_id == user_id,
                ServiceAttendance.service_id == first_service_id,
            )
        )

    assert ended == 0
    assert history is not None
    assert history.ended_at == switched_at


async def test_closed_or_logically_ended_service_rejects_attendance_mutation(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A later cleanup cannot repair attendance committed beyond an interaction end."""

    user_id = await _seed_user(session_factory)
    service_id = await _seed_service(session_factory, key="task6-attendance-closed")
    at_interaction_end = NOW + timedelta(hours=3)

    async with uow_factory() as uow:
        with pytest.raises(ServiceInteractionClosedError):
            await uow.attendances.start_or_switch(
                user_id,
                service_id,
                attendee_kind="ordinary",
                started_at=at_interaction_end,
            )
        assert await uow.services.claim_interaction_closure(
            service_id, now=at_interaction_end
        )
    async with uow_factory() as uow:
        with pytest.raises(ServiceInteractionClosedError):
            await uow.attendances.start_or_switch(
                user_id,
                service_id,
                attendee_kind="ordinary",
                started_at=NOW,
            )


async def test_service_closure_claim_is_due_atomic_and_visible_through_its_dto(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Only one due lifecycle transaction may set the durable closure timestamp."""

    interaction_end = NOW + timedelta(hours=3)
    service_id = await _seed_service(
        session_factory,
        key="task6-closure-claim",
        interaction_ends_at=interaction_end,
    )

    async with uow_factory() as uow:
        assert not await uow.services.claim_interaction_closure(service_id, now=NOW)
    async with uow_factory() as uow:
        assert await uow.services.claim_interaction_closure(
            service_id, now=interaction_end
        )
    async with uow_factory() as uow:
        assert not await uow.services.claim_interaction_closure(
            service_id, now=interaction_end + timedelta(minutes=1)
        )
        service = await uow.services.get(service_id)

    assert service.interaction_closed_at == interaction_end


async def test_logically_ended_service_rejects_selection_application(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """The selection mutation itself must reject a stale pre-closure transition."""

    service_id = await _seed_service(
        session_factory,
        key="task6-selection-ended",
        interaction_ends_at=NOW,
    )
    user_id = await _seed_user(session_factory)
    definition = PublishedFlowDefinition(
        document={"key": "service.closure", "revision": "selection"},
        flow_key_index={"service.closure": ()},
    )
    async with uow_factory() as uow:
        version = await uow.flow_versions.publish(
            definition,
            scope_kind=FlowScopeKind.SERVICE,
            service_id=service_id,
            published_by_user_id=None,
        )
    source = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=version.id,
        parent_flow_key="service.closure",
        service_id=service_id,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("service.closure",),
        checkpoint_flow_keys=("service.closure",),
        opened_at=NOW - timedelta(minutes=1),
        last_focused_at=NOW - timedelta(minutes=1),
    )
    child = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=version.id,
        parent_flow_key="service.closure.child",
        service_id=service_id,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("service.closure", "service.closure.child"),
        checkpoint_flow_keys=("service.closure",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    await _seed_selection(session_factory, source)
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

    async with uow_factory() as uow:
        await uow.lock_user(user_id)
        with pytest.raises(ServiceInteractionClosedError):
            await uow.open_selections.apply(transition, at=NOW)
    async with session_factory() as session:
        selections = list(await session.scalars(select(OpenFlowSelection)))

    assert [selection.id for selection in selections] == [source.id]


async def test_closed_service_rejects_service_match_reservation_without_touching_capacity(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A reservation waiting behind closure must not consume responder capacity."""

    service_id = await _seed_service(session_factory, key="task6-reservation-closed")
    requester_id = await _seed_user(session_factory)
    responder_id = await _seed_user(session_factory, role=OperationalRole.SERVER)
    profile_id = await _seed_profile(
        session_factory, user_id=responder_id, name="task6-reservation"
    )
    request_id = await _seed_request(
        session_factory, requester_user_id=requester_id, service_id=service_id
    )
    at_interaction_end = NOW + timedelta(hours=3)

    async with uow_factory() as uow:
        assert await uow.services.claim_interaction_closure(
            service_id, now=at_interaction_end
        )
    async with uow_factory() as uow:
        with pytest.raises(ServiceInteractionClosedError):
            await uow.matches.reserve_ranked(request_id, [profile_id], now=NOW)
    async with session_factory() as session:
        profile = await session.get(OperationalProfile, profile_id)
        reservations = list(await session.scalars(select(CapacityReservation)))

    assert profile is not None
    assert profile.reserved_capacity == 0
    assert reservations == []


async def test_audiences_use_authoritative_user_roles_and_active_attendance(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Audience expansion must include role inheritance without profile role columns."""

    service_id = await _seed_service(session_factory, key="zone_x_audience")
    nbnc_id = await _seed_user(session_factory, role=OperationalRole.NBNC)
    server_id = await _seed_user(session_factory, role=OperationalRole.SERVER)
    leader_id = await _seed_user(session_factory, role=OperationalRole.LEADER)
    staff_id = await _seed_user(session_factory, role=OperationalRole.STAFF)
    async with uow_factory() as uow:
        await uow.attendances.start_or_switch(
            nbnc_id, service_id, attendee_kind="ordinary", started_at=NOW
        )
        await uow.attendances.start_or_switch(
            server_id, service_id, attendee_kind="ordinary", started_at=NOW
        )
        await uow.attendances.start_or_switch(
            leader_id, service_id, attendee_kind="ordinary", started_at=NOW
        )
        await uow.attendances.start_or_switch(
            staff_id, service_id, attendee_kind="ordinary", started_at=NOW
        )
    async with uow_factory() as uow:
        servers = await uow.services.list_audience_user_ids(
            ServiceAudience.ALL_SERVERS, None, now=NOW
        )
        leaders = await uow.services.list_audience_user_ids(
            ServiceAudience.SERVICE_LEADERS, service_id, now=NOW
        )
        attendees = await uow.services.list_audience_user_ids(
            ServiceAudience.ALL_SERVICE_ATTENDEES, service_id, now=NOW
        )

    assert set(servers) == {server_id, leader_id, staff_id}
    assert set(leaders) == {leader_id, staff_id}
    assert set(attendees) == {nbnc_id, server_id, leader_id, staff_id}


async def test_persona_cannot_advance_to_another_users_message(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A cursor update must reject a restrictive FK target owned by another user."""

    first_user_id = await _seed_user(session_factory)
    second_user_id = await _seed_user(session_factory)
    async with uow_factory() as uow:
        message = await uow.conversations.record_incoming(
            user_id=first_user_id,
            source_message_id=9,
            body="hello",
            replied_to_body=None,
            occurred_at=NOW,
        )
    async with uow_factory() as uow:
        with pytest.raises(ValueError, match="does not belong"):
            await uow.personas.advance(
                second_user_id,
                persona="calm",
                last_message_id=message.id,
                generated_at=NOW,
            )


async def test_match_reservation_can_rematch_after_releasing_a_prior_assignment(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """The guarded update must prevent a second request from overbooking capacity."""

    service_id = await _seed_service(session_factory, key="zone_x_matching")
    requester_id = await _seed_user(session_factory)
    first_responder_id = await _seed_user(session_factory, role=OperationalRole.SERVER)
    second_responder_id = await _seed_user(session_factory, role=OperationalRole.SERVER)
    first_profile_id = await _seed_profile(
        session_factory, user_id=first_responder_id, name="first", capacity=1
    )
    second_profile_id = await _seed_profile(
        session_factory, user_id=second_responder_id, name="second", capacity=1
    )
    first_request_id = await _seed_request(
        session_factory, requester_user_id=requester_id, service_id=service_id
    )
    async with uow_factory() as uow:
        first = await uow.matches.reserve_ranked(
            first_request_id, [first_profile_id], now=NOW
        )
    async with uow_factory() as uow:
        await uow.matches.release_and_exclude(
            first_request_id, first_profile_id, reason="rematch", now=NOW
        )
        retried = await uow.matches.reserve_ranked(
            first_request_id,
            [first_profile_id, second_profile_id],
            now=NOW + timedelta(minutes=1),
        )

    assert first is not None
    assert retried is not None
    assert retried.responder_profile_id == second_profile_id


async def test_service_expiry_releases_only_its_active_match_capacity_once(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A service end must neither exclude nor release another service's matches."""

    expired_service_id = await _seed_service(session_factory, key="task6_expired")
    unrelated_service_id = await _seed_service(session_factory, key="task6_unrelated")
    requester_id = await _seed_user(session_factory)
    target_profile_ids = [
        await _seed_profile(
            session_factory,
            user_id=await _seed_user(session_factory, role=OperationalRole.SERVER),
            name=f"task6-target-{position}",
        )
        for position in range(2)
    ]
    unrelated_profile_id = await _seed_profile(
        session_factory,
        user_id=await _seed_user(session_factory, role=OperationalRole.SERVER),
        name="task6-unrelated",
    )
    unscoped_profile_id = await _seed_profile(
        session_factory,
        user_id=await _seed_user(session_factory, role=OperationalRole.SERVER),
        name="task6-unscoped",
    )
    target_request_ids = [
        await _seed_request(
            session_factory,
            requester_user_id=requester_id,
            service_id=expired_service_id,
        )
        for _ in target_profile_ids
    ]
    unrelated_request_id = await _seed_request(
        session_factory,
        requester_user_id=requester_id,
        service_id=unrelated_service_id,
    )
    unscoped_request_id = await _seed_request(
        session_factory,
        requester_user_id=requester_id,
        service_id=None,
    )
    for request_id, profile_id in zip(target_request_ids, target_profile_ids):
        async with uow_factory() as uow:
            assert (
                await uow.matches.reserve_ranked(request_id, [profile_id], now=NOW)
            ) is not None
    async with uow_factory() as uow:
        assert (
            await uow.matches.reserve_ranked(
                unrelated_request_id, [unrelated_profile_id], now=NOW
            )
        ) is not None
        assert (
            await uow.matches.reserve_ranked(
                unscoped_request_id, [unscoped_profile_id], now=NOW
            )
        ) is not None

    async with uow_factory() as uow:
        released = await uow.matches.release_service_bound(expired_service_id, at=NOW)
    async with uow_factory() as uow:
        released_again = await uow.matches.release_service_bound(
            expired_service_id, at=NOW + timedelta(minutes=1)
        )

    async with session_factory() as session:
        assignments = {
            assignment.request_id: assignment
            for assignment in await session.scalars(select(HumanMatchAssignment))
        }
        reservations = {
            reservation.request_id: reservation
            for reservation in await session.scalars(select(CapacityReservation))
        }
        profiles = {
            profile.id: profile
            for profile in await session.scalars(select(OperationalProfile))
        }
        exclusions = list(await session.scalars(select(HumanMatchExclusion)))

    assert released == 2
    assert released_again == 0
    for request_id in target_request_ids:
        assert assignments[request_id].released_at == NOW
        assert assignments[request_id].release_reason == "service_interaction_ended"
        assert reservations[request_id].released_at == NOW
    assert assignments[unrelated_request_id].released_at is None
    assert assignments[unscoped_request_id].released_at is None
    assert reservations[unrelated_request_id].released_at is None
    assert reservations[unscoped_request_id].released_at is None
    assert all(
        profiles[profile_id].reserved_capacity == 0 for profile_id in target_profile_ids
    )
    assert profiles[unrelated_profile_id].reserved_capacity == 1
    assert profiles[unscoped_profile_id].reserved_capacity == 1
    assert exclusions == []


async def test_service_expiry_returns_unique_affected_users_without_touching_other_branches(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A count-only expiry result would leave I04 unable to return the right users."""

    expired_service_id = await _seed_service(session_factory, key="task6_selection")
    unrelated_service_id = await _seed_service(
        session_factory, key="task6_selection_unrelated"
    )
    first_user_id = await _seed_user(session_factory)
    second_user_id = await _seed_user(session_factory)
    async with uow_factory() as uow:
        expired_version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": "service.expired"},
                flow_key_index={"service.expired": ()},
            ),
            scope_kind=FlowScopeKind.SERVICE,
            service_id=expired_service_id,
            published_by_user_id=None,
        )
        unrelated_version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": "service.unrelated"},
                flow_key_index={"service.unrelated": ()},
            ),
            scope_kind=FlowScopeKind.SERVICE,
            service_id=unrelated_service_id,
            published_by_user_id=None,
        )
    expired_selections = (
        OpenSelectionState(
            id=uuid4(),
            user_id=first_user_id,
            flow_version_id=expired_version.id,
            parent_flow_key="service.expired.first",
            service_id=expired_service_id,
            is_current=True,
            is_global_interruptive=False,
            ancestor_flow_keys=("service.expired", "service.expired.first"),
            checkpoint_flow_keys=("service.expired",),
            opened_at=NOW,
            last_focused_at=NOW,
        ),
        OpenSelectionState(
            id=uuid4(),
            user_id=first_user_id,
            flow_version_id=expired_version.id,
            parent_flow_key="service.expired.second",
            service_id=expired_service_id,
            is_current=True,
            is_global_interruptive=False,
            ancestor_flow_keys=("service.expired", "service.expired.second"),
            checkpoint_flow_keys=("service.expired",),
            opened_at=NOW,
            last_focused_at=NOW,
        ),
        OpenSelectionState(
            id=uuid4(),
            user_id=second_user_id,
            flow_version_id=expired_version.id,
            parent_flow_key="service.expired.third",
            service_id=expired_service_id,
            is_current=True,
            is_global_interruptive=False,
            ancestor_flow_keys=("service.expired", "service.expired.third"),
            checkpoint_flow_keys=("service.expired",),
            opened_at=NOW,
            last_focused_at=NOW,
        ),
    )
    unrelated_selection = OpenSelectionState(
        id=uuid4(),
        user_id=first_user_id,
        flow_version_id=unrelated_version.id,
        parent_flow_key="service.unrelated.first",
        service_id=unrelated_service_id,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("service.unrelated", "service.unrelated.first"),
        checkpoint_flow_keys=("service.unrelated",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    for selection in (*expired_selections, unrelated_selection):
        await _seed_selection(session_factory, selection)

    async with uow_factory() as uow:
        expiry = await uow.open_selections.expire_service_bound(
            expired_service_id, at=NOW
        )
    async with uow_factory() as uow:
        repeated_expiry = await uow.open_selections.expire_service_bound(
            expired_service_id, at=NOW + timedelta(minutes=1)
        )
    async with session_factory() as session:
        rows = {
            selection.id: selection
            for selection in await session.scalars(select(OpenFlowSelection))
        }

    assert expiry.expired_selection_count == 3
    assert expiry.affected_user_ids == frozenset({first_user_id, second_user_id})
    assert repeated_expiry.expired_selection_count == 0
    assert repeated_expiry.affected_user_ids == frozenset()
    for selection in expired_selections:
        assert rows[selection.id].expires_at == NOW
        assert rows[selection.id].is_current is False
    assert rows[unrelated_selection.id].expires_at is None
    assert rows[unrelated_selection.id].is_current is True


async def test_open_selection_apply_persists_transition_rows_without_actions(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Applying a transition must write only its state mutation, never execute actions."""

    user_id = await _seed_user(session_factory)
    definition = PublishedFlowDefinition(
        document={"key": "system.home", "next_flows": []},
        flow_key_index={"system.home": ()},
    )
    async with uow_factory() as uow:
        version = await uow.flow_versions.publish(
            definition,
            scope_kind=FlowScopeKind.SYSTEM,
            service_id=None,
            published_by_user_id=None,
        )
    source = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=version.id,
        parent_flow_key="system.home",
        service_id=None,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("system.home",),
        checkpoint_flow_keys=("system.home",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    selection = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=version.id,
        parent_flow_key="system.home.directions",
        service_id=None,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("system.home", "system.home.directions"),
        checkpoint_flow_keys=("system.home",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    await _seed_selection(session_factory, source)
    transition = SelectionTransition(
        source_selection_id=source.id,
        delete_selection_ids=frozenset(),
        reusable_past_selection_ids=frozenset({source.id}),
        current_selection_ids=frozenset({selection.parent_flow_key}),
        upsert_selections=(selection,),
        child_actions=(),
        checkpoint_return=None,
        generic_leaf_return_suppressed=False,
    )

    async with uow_factory() as uow:
        await uow.lock_user(user_id)
        await uow.open_selections.apply(transition, at=NOW)
    async with uow_factory() as uow:
        selections = await uow.open_selections.list_for_user(user_id, now=NOW)

    assert {stored.id for stored in selections} == {source.id, selection.id}
    assert (
        next(stored for stored in selections if stored.id == selection.id) == selection
    )


async def test_open_root_is_idempotent_and_preserves_another_current_branch(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Automatic timestamp roots must not replace a pending onboarding branch."""

    user_id = await _seed_user(session_factory)
    service_id = await _seed_service(session_factory, key="task8-timestamp-root")
    async with uow_factory() as uow:
        onboarding_version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": "system.onboarding.name_capture"},
                flow_key_index={"system.onboarding.name_capture": ()},
            ),
            scope_kind=FlowScopeKind.SYSTEM,
            service_id=None,
            published_by_user_id=None,
        )
        timestamp_version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": "service.zone_x.timestamp.service_questions"},
                flow_key_index={"service.zone_x.timestamp.service_questions": ()},
            ),
            scope_kind=FlowScopeKind.SERVICE,
            service_id=service_id,
            published_by_user_id=None,
        )
    onboarding = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=onboarding_version.id,
        parent_flow_key="system.onboarding.name_capture",
        service_id=None,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("system.onboarding.name_capture",),
        checkpoint_flow_keys=("system.onboarding.name_capture",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    await _seed_selection(session_factory, onboarding)
    timestamp_root = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=timestamp_version.id,
        parent_flow_key="service.zone_x.timestamp.service_questions",
        service_id=service_id,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("service.zone_x.timestamp.service_questions",),
        checkpoint_flow_keys=("service.zone_x.timestamp.service_questions",),
        opened_at=NOW,
        last_focused_at=NOW,
    )

    async with uow_factory() as uow:
        await uow.lock_user(user_id)
        opened = await uow.open_selections.open_root(timestamp_root, at=NOW)
        reopened = await uow.open_selections.open_root(
            OpenSelectionState(
                id=uuid4(),
                user_id=user_id,
                flow_version_id=timestamp_version.id,
                parent_flow_key="service.zone_x.timestamp.service_questions",
                service_id=service_id,
                is_current=True,
                is_global_interruptive=False,
                ancestor_flow_keys=("service.zone_x.timestamp.service_questions",),
                checkpoint_flow_keys=("service.zone_x.timestamp.service_questions",),
                opened_at=NOW + timedelta(minutes=1),
                last_focused_at=NOW + timedelta(minutes=1),
            ),
            at=NOW + timedelta(minutes=1),
        )

    async with uow_factory() as uow:
        selections = await uow.open_selections.list_for_user(user_id, now=NOW)

    assert opened == timestamp_root
    assert reopened == timestamp_root
    assert {
        (selection.parent_flow_key, selection.is_current) for selection in selections
    } == {
        ("system.onboarding.name_capture", True),
        ("service.zone_x.timestamp.service_questions", True),
    }


async def test_open_selection_apply_rejects_an_upsert_without_its_user_lock(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """An upsert-only transition must not bypass the per-user processing lock."""

    user_id = await _seed_user(session_factory)
    definition = PublishedFlowDefinition(
        document={"key": "system.home", "revision": "lock"},
        flow_key_index={"system.home": ()},
    )
    async with uow_factory() as uow:
        version = await uow.flow_versions.publish(
            definition,
            scope_kind=FlowScopeKind.SYSTEM,
            service_id=None,
            published_by_user_id=None,
        )
    source = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=version.id,
        parent_flow_key="system.home",
        service_id=None,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("system.home",),
        checkpoint_flow_keys=("system.home",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    child = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=version.id,
        parent_flow_key="system.home.directions",
        service_id=None,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("system.home", "system.home.directions"),
        checkpoint_flow_keys=("system.home",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    await _seed_selection(session_factory, source)
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

    async with uow_factory() as uow:
        with pytest.raises(RuntimeError, match="locked"):
            await uow.open_selections.apply(transition, at=NOW)


async def test_leaf_return_scopes_checkpoint_focus_to_its_source_branch(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A same-key checkpoint in another version or service branch must stay untouched."""

    user_id = await _seed_user(session_factory)
    service_id = await _seed_service(session_factory, key="zone_x_branch_scope")
    definitions = (
        PublishedFlowDefinition(
            document={"key": "system.home", "revision": "source"},
            flow_key_index={"system.home": ()},
        ),
        PublishedFlowDefinition(
            document={"key": "system.home", "revision": "other-version"},
            flow_key_index={"system.home": ()},
        ),
        PublishedFlowDefinition(
            document={"key": "system.home", "revision": "service"},
            flow_key_index={"system.home": ()},
        ),
    )
    async with uow_factory() as uow:
        source_version = await uow.flow_versions.publish(
            definitions[0],
            scope_kind=FlowScopeKind.SYSTEM,
            service_id=None,
            published_by_user_id=None,
        )
        other_version = await uow.flow_versions.publish(
            definitions[1],
            scope_kind=FlowScopeKind.SYSTEM,
            service_id=None,
            published_by_user_id=None,
        )
        service_version = await uow.flow_versions.publish(
            definitions[2],
            scope_kind=FlowScopeKind.SERVICE,
            service_id=service_id,
            published_by_user_id=None,
        )
    source = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=source_version.id,
        parent_flow_key="system.home.leaf",
        service_id=None,
        is_current=False,
        is_global_interruptive=False,
        ancestor_flow_keys=("system.home", "system.home.leaf"),
        checkpoint_flow_keys=("system.home",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    target = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=source_version.id,
        parent_flow_key="system.home",
        service_id=None,
        is_current=False,
        is_global_interruptive=False,
        ancestor_flow_keys=("system.home",),
        checkpoint_flow_keys=("system.home",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    other_version_target = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=other_version.id,
        parent_flow_key="system.home",
        service_id=None,
        is_current=False,
        is_global_interruptive=False,
        ancestor_flow_keys=("system.home",),
        checkpoint_flow_keys=("system.home",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    service_target = OpenSelectionState(
        id=uuid4(),
        user_id=user_id,
        flow_version_id=service_version.id,
        parent_flow_key="system.home",
        service_id=service_id,
        is_current=False,
        is_global_interruptive=False,
        ancestor_flow_keys=("system.home",),
        checkpoint_flow_keys=("system.home",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    for selection in (source, target, other_version_target, service_target):
        await _seed_selection(session_factory, selection)
    checkpoint_return = CheckpointReturnTransition(
        source_selection_id=source.id,
        target_checkpoint_key="system.home",
        current_selection_ids=frozenset({"system.home"}),
        reusable_past_selection_ids=frozenset(),
        return_actions=(),
        focused_at=NOW + timedelta(minutes=1),
    )
    transition = SelectionTransition(
        source_selection_id=source.id,
        delete_selection_ids=frozenset(),
        reusable_past_selection_ids=frozenset(),
        current_selection_ids=frozenset({"system.home"}),
        upsert_selections=(),
        child_actions=(),
        checkpoint_return=checkpoint_return,
        generic_leaf_return_suppressed=False,
    )

    async with uow_factory() as uow:
        await uow.lock_user(user_id)
        await uow.open_selections.apply(transition, at=NOW)
    async with session_factory() as session:
        rows = await session.scalars(
            select(OpenFlowSelection).where(
                OpenFlowSelection.id.in_(
                    (target.id, other_version_target.id, service_target.id)
                )
            )
        )
        current_by_id = {row.id: row.is_current for row in rows}

    assert current_by_id == {
        target.id: True,
        other_version_target.id: False,
        service_target.id: False,
    }


async def test_concurrent_capacity_guard_allows_only_one_reservation(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Two requests racing for one slot must leave exactly one durable reservation."""

    service_id = await _seed_service(session_factory, key="zone_x_capacity_race")
    requester_id = await _seed_user(session_factory)
    responder_id = await _seed_user(session_factory, role=OperationalRole.SERVER)
    profile_id = await _seed_profile(
        session_factory, user_id=responder_id, name="capacity", capacity=1
    )
    requests = [
        await _seed_request(
            session_factory, requester_user_id=requester_id, service_id=service_id
        )
        for _ in range(2)
    ]

    async def reserve(request_id: UUID):
        async with uow_factory() as uow:
            return await uow.matches.reserve_ranked(request_id, [profile_id], now=NOW)

    assignments = await asyncio.gather(
        *(reserve(request_id) for request_id in requests)
    )
    async with session_factory() as session:
        profile = await session.get(OperationalProfile, profile_id)

    assert sum(assignment is not None for assignment in assignments) == 1
    assert profile is not None
    assert profile.reserved_capacity == 1


async def test_concurrent_timestamp_claim_has_exactly_one_winner(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Two scheduler transactions must not claim the same timestamp recipient twice."""

    user_id = await _seed_user(session_factory)
    service_id = await _seed_service(session_factory, key="zone_x_timestamp_race")
    definition = PublishedFlowDefinition(
        document={"key": "service.timestamp.notice", "revision": "claim"},
        flow_key_index={"service.timestamp.notice": ()},
    )
    async with uow_factory() as uow:
        version = await uow.flow_versions.publish(
            definition,
            scope_kind=FlowScopeKind.SERVICE,
            service_id=service_id,
            published_by_user_id=None,
        )
    timestamp_id = uuid4()
    async with session_factory.begin() as session:
        session.add(
            ServiceTimestamp(
                id=timestamp_id,
                service_id=service_id,
                key="notice",
                occurs_at=NOW,
                audience=ServiceAudience.ALL_NBNCS,
                flow_version_id=version.id,
                root_flow_key="service.timestamp.notice",
            )
        )

    async def claim() -> bool:
        async with uow_factory() as uow:
            return await uow.deliveries.claim_timestamp_delivery(
                timestamp_id, user_id, now=NOW
            )

    winners = await asyncio.gather(claim(), claim())

    assert winners.count(True) == 1


async def test_timestamp_delivery_claim_is_idempotent(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A retry after a committed claim must not obtain timestamp work again."""

    user_id = await _seed_user(session_factory)
    service_id = await _seed_service(session_factory, key="zone_x_timestamp_retry")
    definition = PublishedFlowDefinition(
        document={"key": "service.timestamp.notice", "revision": "idempotent"},
        flow_key_index={"service.timestamp.notice": ()},
    )
    async with uow_factory() as uow:
        version = await uow.flow_versions.publish(
            definition,
            scope_kind=FlowScopeKind.SERVICE,
            service_id=service_id,
            published_by_user_id=None,
        )
    timestamp_id = uuid4()
    async with session_factory.begin() as session:
        session.add(
            ServiceTimestamp(
                id=timestamp_id,
                service_id=service_id,
                key="retry",
                occurs_at=NOW,
                audience=ServiceAudience.ALL_NBNCS,
                flow_version_id=version.id,
                root_flow_key="service.timestamp.notice",
            )
        )

    async with uow_factory() as uow:
        assert await uow.deliveries.claim_timestamp_delivery(
            timestamp_id, user_id, now=NOW
        )
    async with uow_factory() as uow:
        assert not await uow.deliveries.claim_timestamp_delivery(
            timestamp_id, user_id, now=NOW
        )


async def test_timestamp_claim_at_interaction_boundary_creates_no_durable_claim(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A claim cannot outlive the same durable interaction boundary as attendance."""

    user_id = await _seed_user(session_factory)
    service_id = await _seed_service(
        session_factory,
        key="zone_x_timestamp_closed_boundary",
        interaction_ends_at=NOW,
    )
    async with uow_factory() as uow:
        version = await uow.flow_versions.publish(
            PublishedFlowDefinition(
                document={"key": "service.timestamp.closed"},
                flow_key_index={"service.timestamp.closed": ()},
            ),
            scope_kind=FlowScopeKind.SERVICE,
            service_id=service_id,
            published_by_user_id=None,
        )
    timestamp_id = uuid4()
    async with session_factory.begin() as session:
        session.add(
            ServiceTimestamp(
                id=timestamp_id,
                service_id=service_id,
                key="closed-boundary",
                occurs_at=NOW - timedelta(minutes=1),
                audience=ServiceAudience.ALL_NBNCS,
                flow_version_id=version.id,
                root_flow_key="service.timestamp.closed",
            )
        )

    with pytest.raises(ServiceInteractionClosedError):
        async with uow_factory() as uow:
            await uow.deliveries.claim_timestamp_delivery(
                timestamp_id, user_id, now=NOW
            )

    async with session_factory() as session:
        claims = list(
            await session.scalars(
                select(TimestampDeliveryClaim).where(
                    TimestampDeliveryClaim.service_timestamp_id == timestamp_id
                )
            )
        )

    assert claims == []


async def test_delivery_claim_exposes_a_provider_neutral_immutable_message(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """The worker must receive all send inputs without reaching into an ORM row."""

    user_id = await _seed_user(session_factory)
    delivery = NewOutboundDelivery(
        idempotency_key="delivery:message",
        user_id=user_id,
        telegram_chat_id=71,
        kind="message",
        payload={"text": "hello", "buttons": [{"label": "Continue"}]},
    )
    async with uow_factory() as uow:
        enqueued = await uow.deliveries.enqueue(delivery)

    async with uow_factory() as first:
        claimed = await first.deliveries.claim_next_safe(now=NOW)

        async def contend() -> object:
            async with uow_factory() as second:
                return await second.deliveries.claim_next_safe(now=NOW)

        assert await contend() is None
        assert claimed is not None
        assert claimed.id == enqueued.id
        assert claimed.status == "claimed"
        assert claimed.message.chat_id == 71
        assert claimed.message.kind == "message"
        assert dict(claimed.message.payload) == {
            "text": "hello",
            "buttons": ({"label": "Continue"},),
        }
        with pytest.raises(TypeError):
            claimed.message.payload["text"] = "changed"
        buttons = claimed.message.payload["buttons"]
        assert isinstance(buttons, tuple)
        assert isinstance(buttons[0], dict) is False
        with pytest.raises(TypeError):
            buttons[0]["label"] = "changed"
        assert claimed.claim_token is not None
        assert claimed.claim_expires_at is not None

    async with session_factory() as session:
        stored = await session.get(OutboundDelivery, enqueued.id)

    assert stored is not None
    assert stored.status == "claimed"


async def test_delivery_retry_at_is_exact_and_attempt_result_metadata_persists(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A parsed retry delay must gate the next claim and retain safe result details."""

    user_id = await _seed_user(session_factory)
    retry_at = NOW + timedelta(minutes=7)
    delivery = NewOutboundDelivery(
        idempotency_key="delivery:exact-retry",
        user_id=user_id,
        telegram_chat_id=72,
        kind="message",
        payload={"text": "retry later"},
        eligible_at=NOW,
    )
    async with uow_factory() as uow:
        enqueued = await uow.deliveries.enqueue(delivery)
    async with uow_factory() as uow:
        claim = await uow.deliveries.claim_next_safe(now=NOW)
        assert claim is not None
        assert claim.claim_token is not None
        correlation_id = uuid4()
        first_attempt = await uow.deliveries.start_attempt(
            claim.id,
            correlation_id,
            claim_token=claim.claim_token,
            started_at=NOW,
        )
        await uow.deliveries.finish_attempt(
            claim.id,
            first_attempt.id,
            "retry",
            now=NOW + timedelta(seconds=1),
            retry_at=retry_at,
            safe_error="telegram_rate_limited",
        )

    async with session_factory() as session:
        stored = await session.get(OutboundDelivery, enqueued.id)
        attempts = list(
            await session.scalars(
                select(OutboundDeliveryAttempt)
                .where(OutboundDeliveryAttempt.delivery_id == enqueued.id)
                .order_by(OutboundDeliveryAttempt.attempt_number)
            )
        )

    assert stored is not None
    assert stored.status == "retry"
    assert stored.eligible_at == retry_at
    assert stored.claim_token is None
    assert stored.claim_expires_at is None
    assert [(attempt.correlation_id, attempt.safe_error) for attempt in attempts] == [
        (correlation_id, "telegram_rate_limited")
    ]

    async with uow_factory() as uow:
        assert (
            await uow.deliveries.claim_next_safe(
                now=retry_at - timedelta(microseconds=1)
            )
            is None
        )
    async with uow_factory() as uow:
        retry_claim = await uow.deliveries.claim_next_safe(now=retry_at)
        assert retry_claim is not None
        assert retry_claim.claim_token is not None
        completed_attempt = await uow.deliveries.start_attempt(
            retry_claim.id,
            uuid4(),
            claim_token=retry_claim.claim_token,
            started_at=retry_at,
        )
        await uow.deliveries.finish_attempt(
            retry_claim.id,
            completed_attempt.id,
            "sent",
            now=retry_at,
            confirmed_telegram_message_id=9182,
        )
    async with session_factory() as session:
        stored = await session.get(OutboundDelivery, enqueued.id)

    assert stored is not None
    assert stored.status == "sent"
    assert stored.confirmed_telegram_message_id == 9182

    immediate = NewOutboundDelivery(
        idempotency_key="delivery:legacy-immediate-retry",
        user_id=user_id,
        telegram_chat_id=72,
        kind="message",
        payload={"text": "retry now"},
        eligible_at=NOW,
    )
    async with uow_factory() as uow:
        immediate_record = await uow.deliveries.enqueue(immediate)
    async with uow_factory() as uow:
        immediate_claim = await uow.deliveries.claim_next_safe(now=NOW)
        assert immediate_claim is not None
        assert immediate_claim.claim_token is not None
        immediate_attempt = await uow.deliveries.start_attempt(
            immediate_claim.id,
            uuid4(),
            claim_token=immediate_claim.claim_token,
            started_at=NOW,
        )
        await uow.deliveries.finish_attempt(
            immediate_claim.id,
            immediate_attempt.id,
            "retry",
            now=NOW,
        )
    async with uow_factory() as uow:
        immediate_retry = await uow.deliveries.claim_next_safe(now=NOW)

    assert immediate_retry is not None
    assert immediate_retry.id == immediate_record.id


async def test_only_an_expired_unstarted_claim_is_recoverable_and_stale_tokens_are_fenced(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Recovery must never replay a started or uncertain network boundary crossing."""

    user_id = await _seed_user(session_factory)
    lease_duration = timedelta(minutes=1)

    def new_delivery(idempotency_key: str) -> NewOutboundDelivery:
        return NewOutboundDelivery(
            idempotency_key=idempotency_key,
            user_id=user_id,
            telegram_chat_id=73,
            kind="message",
            payload={"text": idempotency_key},
            eligible_at=NOW,
        )

    async with uow_factory() as uow:
        unstarted = await uow.deliveries.enqueue(new_delivery("delivery:unstarted"))
    async with uow_factory() as uow:
        unstarted_claim = await uow.deliveries.claim_next_safe(
            now=NOW, lease_duration=lease_duration
        )
        assert unstarted_claim is not None
        assert unstarted_claim.id == unstarted.id
        assert unstarted_claim.claim_token is not None
        stale_token = unstarted_claim.claim_token
        renewed = await uow.deliveries.renew_claim(
            unstarted_claim.id,
            claim_token=stale_token,
            now=NOW + timedelta(seconds=1),
            lease_duration=lease_duration,
        )
        assert renewed.claim_expires_at == NOW + timedelta(minutes=1, seconds=1)
        with pytest.raises(DeliveryClaimLostError):
            await uow.deliveries.renew_claim(
                unstarted_claim.id,
                claim_token=uuid4(),
                now=NOW + timedelta(seconds=1),
                lease_duration=lease_duration,
            )

    async with uow_factory() as uow:
        started = await uow.deliveries.enqueue(new_delivery("delivery:started"))
    async with uow_factory() as uow:
        started_claim = await uow.deliveries.claim_next_safe(
            now=NOW + timedelta(seconds=2), lease_duration=lease_duration
        )
        assert started_claim is not None
        assert started_claim.id == started.id
        assert started_claim.claim_token is not None
        await uow.deliveries.start_attempt(
            started_claim.id,
            uuid4(),
            claim_token=started_claim.claim_token,
            started_at=NOW + timedelta(seconds=2),
        )
    async with uow_factory() as uow:
        uncertain = await uow.deliveries.enqueue(new_delivery("delivery:uncertain"))
    async with uow_factory() as uow:
        uncertain_claim = await uow.deliveries.claim_next_safe(
            now=NOW + timedelta(seconds=3), lease_duration=lease_duration
        )
        assert uncertain_claim is not None
        assert uncertain_claim.id == uncertain.id
        assert uncertain_claim.claim_token is not None
        uncertain_attempt = await uow.deliveries.start_attempt(
            uncertain_claim.id,
            uuid4(),
            claim_token=uncertain_claim.claim_token,
            started_at=NOW + timedelta(seconds=3),
        )
        await uow.deliveries.finish_attempt(
            uncertain_claim.id,
            uncertain_attempt.id,
            "uncertain",
            now=NOW + timedelta(seconds=4),
            safe_error="telegram_transport_error",
        )

    async with uow_factory() as uow:
        recovered = await uow.deliveries.claim_next_safe(
            now=NOW + timedelta(minutes=1, seconds=1),
            lease_duration=lease_duration,
        )
        assert recovered is not None
        assert recovered.id == unstarted.id
        assert recovered.claim_token is not None
        assert recovered.claim_token != stale_token
        with pytest.raises(DeliveryClaimLostError):
            await uow.deliveries.renew_claim(
                unstarted.id,
                claim_token=stale_token,
                now=NOW + timedelta(minutes=1, seconds=1),
                lease_duration=lease_duration,
            )
        with pytest.raises(DeliveryClaimLostError):
            await uow.deliveries.start_attempt(
                unstarted.id,
                uuid4(),
                claim_token=stale_token,
                started_at=NOW + timedelta(minutes=1, seconds=1),
            )

    async with uow_factory() as uow:
        assert (
            await uow.deliveries.claim_next_safe(
                now=NOW + timedelta(minutes=1, seconds=2),
                lease_duration=lease_duration,
            )
            is None
        )


async def test_recovered_claim_rejects_stale_token_renewal(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Recovery must fence the previous owner from renewing an unstarted claim."""

    user_id = await _seed_user(session_factory)
    lease_duration = timedelta(minutes=1)
    delivery = NewOutboundDelivery(
        idempotency_key="delivery:stale-renewal",
        user_id=user_id,
        telegram_chat_id=74,
        kind="message",
        payload={"text": "stale renewal"},
        eligible_at=NOW,
    )
    async with uow_factory() as uow:
        enqueued = await uow.deliveries.enqueue(delivery)
    async with uow_factory() as uow:
        first_claim = await uow.deliveries.claim_next_safe(
            now=NOW, lease_duration=lease_duration
        )

    assert first_claim is not None
    assert first_claim.claim_token is not None
    stale_token = first_claim.claim_token

    async with uow_factory() as uow:
        recovered_claim = await uow.deliveries.claim_next_safe(
            now=NOW + lease_duration, lease_duration=lease_duration
        )
        assert recovered_claim is not None
        assert recovered_claim.id == enqueued.id
        assert recovered_claim.claim_token is not None
        assert recovered_claim.claim_token != stale_token
        with pytest.raises(DeliveryClaimLostError):
            await uow.deliveries.renew_claim(
                enqueued.id,
                claim_token=stale_token,
                now=NOW + lease_duration,
                lease_duration=lease_duration,
            )


async def test_flow_publication_and_delivery_enqueue_are_idempotent(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Repeated canonical publication or outbound enqueue must return one durable row."""

    user_id = await _seed_user(session_factory)
    definition = PublishedFlowDefinition(
        document={"key": "system.home", "next_flows": []},
        flow_key_index={"system.home": ()},
    )
    delivery = NewOutboundDelivery(
        idempotency_key="welcome:one",
        user_id=user_id,
        telegram_chat_id=500,
        kind="message",
        payload={"text": "hello"},
    )

    async with uow_factory() as uow:
        first_version = await uow.flow_versions.publish(
            definition,
            scope_kind=FlowScopeKind.SYSTEM,
            service_id=None,
            published_by_user_id=None,
        )
        first_delivery = await uow.deliveries.enqueue(delivery)
    async with uow_factory() as uow:
        second_version = await uow.flow_versions.publish(
            definition,
            scope_kind=FlowScopeKind.SYSTEM,
            service_id=None,
            published_by_user_id=None,
        )
        second_delivery = await uow.deliveries.enqueue(delivery)

    assert second_version.id == first_version.id
    assert second_version.definition == definition.document
    assert second_delivery == first_delivery
