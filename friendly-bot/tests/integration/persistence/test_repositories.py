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
    FlowScopeKind,
    HumanMatchRequest,
    OpenFlowSelection,
    OperationalProfile,
    OperationalRole,
    OutboundDelivery,
    OutboundDeliveryAttempt,
    Service,
    ServiceAudience,
    ServiceTimestamp,
    User,
)
from friendly_bot.persistence.repositories import NewOutboundDelivery
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
                interaction_ends_at=NOW + timedelta(hours=3),
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
        assert (
            await uow.operational_logins.attach(profile_id, first_user_id, at=NOW)
            == "attached"
        )
        assert (
            await uow.operational_logins.attach(profile_id, second_user_id, at=NOW)
            == "occupied"
        )
        user = await uow.users.require_by_telegram_id(101)
        profile = await uow.operational_profiles.find_by_login_identity(
            "jordan", date(1990, 1, 1)
        )

    assert user.role is OperationalRole.NBNC
    assert profile is not None
    assert not hasattr(profile, "role")


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
        await uow.open_selections.apply(transition)
    async with uow_factory() as uow:
        selections = await uow.open_selections.list_for_user(user_id, now=NOW)

    assert {stored.id for stored in selections} == {source.id, selection.id}
    assert (
        next(stored for stored in selections if stored.id == selection.id) == selection
    )


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
            await uow.open_selections.apply(transition)


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
        await uow.open_selections.apply(transition)
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
            return await uow.deliveries.claim_timestamp_delivery(timestamp_id, user_id)

    winners = await asyncio.gather(claim(), claim())

    assert winners.count(True) == 1


async def test_delivery_claim_skips_an_inflight_row_and_records_attempt_outcomes(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A claim lease must skip a locked row and attempts must advance durable state."""

    user_id = await _seed_user(session_factory)
    delivery = NewOutboundDelivery(
        idempotency_key="delivery:lease",
        user_id=user_id,
        telegram_chat_id=71,
        kind="message",
        payload={"text": "hello"},
    )
    async with uow_factory() as uow:
        enqueued = await uow.deliveries.enqueue(delivery)
    async with session_factory.begin() as session:
        await session.execute(
            OutboundDelivery.__table__.update()
            .where(OutboundDelivery.id == enqueued.id)
            .values(created_at=NOW + timedelta(minutes=1))
        )
    async with uow_factory() as uow:
        assert await uow.deliveries.claim_next_safe(now=NOW) is None
    async with session_factory.begin() as session:
        await session.execute(
            OutboundDelivery.__table__.update()
            .where(OutboundDelivery.id == enqueued.id)
            .values(created_at=NOW)
        )

    async with uow_factory() as first:
        claimed = await first.deliveries.claim_next_safe(now=NOW)

        async def contend() -> object:
            async with uow_factory() as second:
                return await second.deliveries.claim_next_safe(now=NOW)

        assert await contend() is None
        assert claimed is not None
        assert claimed.id == enqueued.id
        assert claimed.status == "claimed"

    async with uow_factory() as uow:
        first_attempt = await uow.deliveries.start_attempt(
            enqueued.id, uuid4(), started_at=NOW
        )
        await uow.deliveries.finish_attempt(
            enqueued.id, first_attempt.id, "retry", now=NOW + timedelta(minutes=1)
        )
    async with uow_factory() as uow:
        assert await uow.deliveries.claim_next_safe(now=NOW + timedelta(minutes=2))
        second_attempt = await uow.deliveries.start_attempt(
            enqueued.id, uuid4(), started_at=NOW + timedelta(minutes=2)
        )
        await uow.deliveries.finish_attempt(
            enqueued.id, second_attempt.id, "sent", now=NOW + timedelta(minutes=3)
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
    assert stored.status == "sent"
    assert stored.sent_at == NOW + timedelta(minutes=3)
    assert [(attempt.attempt_number, attempt.outcome) for attempt in attempts] == [
        (1, "retry"),
        (2, "sent"),
    ]


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
