"""Transaction semantics for the F01 asynchronous unit of work."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import Enum, MetaData
from sqlalchemy.dialects.postgresql import CreateEnumType, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.domain.state import OpenSelectionState, SelectionTransition
from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import (
    FlowScopeKind,
    OpenFlowSelection,
    OperationalRole,
    User,
)
from friendly_bot.persistence.uow import UnitOfWork

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_SESSION_FACTORY = async_sessionmaker[AsyncSession]


class SimulatedActionFailure(RuntimeError):
    """An executor-side failure after state mutation but before transaction commit."""


async def _create_schema(
    connection: asyncpg.Connection[asyncpg.Record], schema: str
) -> None:
    """Create the F01 metadata in a disposable schema for UoW tests."""

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
    """Provide schema-isolated factories rather than sharing transactions between tests."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")
    url = make_url(database_url)
    schema = f"friendly_bot_task6_uow_{uuid4().hex}"
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
    """Construct a fresh UoW for each independently committed transaction."""

    return lambda: UnitOfWork(session_factory)


async def _seed_user_and_version(
    session_factory: _SESSION_FACTORY, factory: Callable[[], UnitOfWork]
) -> tuple[OpenSelectionState, UUID]:
    user_id = uuid4()
    async with session_factory.begin() as session:
        session.add(User(id=user_id, role=OperationalRole.NBNC))
    definition = PublishedFlowDefinition(
        document={"key": "system.home", "next_flows": []},
        flow_key_index={"system.home": ()},
    )
    async with factory() as uow:
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
    return source, version.id


def _selection_transition(
    source: OpenSelectionState, flow_version_id: UUID
) -> SelectionTransition:
    selection = OpenSelectionState(
        id=uuid4(),
        user_id=source.user_id,
        flow_version_id=flow_version_id,
        parent_flow_key="system.home.directions",
        service_id=None,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("system.home", "system.home.directions"),
        checkpoint_flow_keys=("system.home",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    return SelectionTransition(
        source_selection_id=source.id,
        delete_selection_ids=frozenset(),
        reusable_past_selection_ids=frozenset({source.id}),
        current_selection_ids=frozenset({selection.parent_flow_key}),
        upsert_selections=(selection,),
        child_actions=(),
        checkpoint_return=None,
        generic_leaf_return_suppressed=False,
    )


async def test_uow_rolls_back_open_selection_when_action_effect_fails(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """An executor failure must leave selection mutation uncommitted for retry."""

    source, flow_version_id = await _seed_user_and_version(session_factory, uow_factory)
    transition = _selection_transition(source, flow_version_id)

    with pytest.raises(SimulatedActionFailure):
        async with uow_factory() as uow:
            await uow.lock_user(source.user_id)
            await uow.open_selections.apply(transition, at=NOW)
            raise SimulatedActionFailure("executor failed before commit")
    async with uow_factory() as verify:
        assert await verify.open_selections.list_for_user(source.user_id, now=NOW) == [
            source
        ]


async def test_uow_commits_user_resolution_only_without_an_exception(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A successful block is the only path that makes a newly resolved sender durable."""

    async with uow_factory() as uow:
        created = await uow.users.resolve_telegram_sender(81, received_at=NOW)
    async with uow_factory() as uow:
        retrieved = await uow.users.require_by_telegram_id(81)

    assert retrieved == created


async def test_reused_uow_does_not_retain_a_previous_user_lock(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A second block must explicitly lock its user instead of inheriting old scope."""

    source, flow_version_id = await _seed_user_and_version(session_factory, uow_factory)
    transition = _selection_transition(source, flow_version_id)
    uow = uow_factory()

    async with uow:
        await uow.lock_user(source.user_id)
    async with uow:
        with pytest.raises(RuntimeError, match="locked"):
            await uow.open_selections.apply(transition, at=NOW)


async def test_concurrent_update_claim_has_exactly_one_winner(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Concurrent UoWs must serialize an idempotent incoming-update claim in PostgreSQL."""

    async def claim() -> bool:
        async with uow_factory() as uow:
            return await uow.updates.claim_update(871, received_at=NOW)

    winners = await asyncio.gather(claim(), claim())

    assert winners.count(True) == 1
