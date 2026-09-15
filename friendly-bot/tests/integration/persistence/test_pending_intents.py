"""PostgreSQL contracts for ordered, bounded pending flow intents."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import Enum, MetaData
from sqlalchemy.dialects.postgresql import CreateEnumType, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import FlowScopeKind, OperationalRole, User
from friendly_bot.persistence.repositories import NewPendingFlowIntent
from friendly_bot.persistence.uow import UnitOfWork

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_SESSION_FACTORY = async_sessionmaker[AsyncSession]


async def _create_schema(
    connection: asyncpg.Connection[asyncpg.Record], schema: str
) -> None:
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
    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")
    url = make_url(database_url)
    schema = f"friendly_bot_pending_intents_{uuid4().hex}"
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
    return lambda: UnitOfWork(session_factory)


async def _seed_user_and_version(
    session_factory: _SESSION_FACTORY, factory: Callable[[], UnitOfWork]
) -> tuple[UUID, UUID]:
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
    return user_id, version.id


def _intent(
    user_id: UUID, version_id: UUID, key: str, *, at: datetime = NOW
) -> NewPendingFlowIntent:
    return NewPendingFlowIntent(
        user_id=user_id,
        flow_key=key,
        flow_version_id=version_id,
        service_id=None,
        created_at=at,
        expires_at=at + timedelta(hours=24),
    )


async def test_pending_intents_require_a_user_lock(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    user_id, version_id = await _seed_user_and_version(session_factory, uow_factory)

    async with uow_factory() as uow:
        with pytest.raises(RuntimeError, match="pending intent user is not locked"):
            await uow.pending_intents.append(
                _intent(user_id, version_id, "flow.one"), max_per_user=5
            )


async def test_pending_intents_refresh_duplicates_cap_and_isolate_users(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    first_user, version_id = await _seed_user_and_version(session_factory, uow_factory)
    second_user, _ = await _seed_user_and_version(session_factory, uow_factory)

    async with uow_factory() as uow:
        await uow.lock_user(first_user)
        for index in range(5):
            await uow.pending_intents.append(
                _intent(first_user, version_id, f"flow.{index}"), max_per_user=5
            )
        refreshed = await uow.pending_intents.append(
            _intent(first_user, version_id, "flow.1", at=NOW + timedelta(hours=1)),
            max_per_user=5,
        )
        await uow.pending_intents.append(
            _intent(first_user, version_id, "flow.5", at=NOW + timedelta(hours=2)),
            max_per_user=5,
        )
        await uow.lock_user(second_user)
        await uow.pending_intents.append(
            _intent(second_user, version_id, "flow.1"), max_per_user=5
        )

    async with uow_factory() as uow:
        first = await uow.pending_intents.list_active(
            first_user, now=NOW + timedelta(hours=2)
        )
        second = await uow.pending_intents.list_active(
            second_user, now=NOW + timedelta(hours=2)
        )

    assert [record.flow_key for record in first] == [
        "flow.2",
        "flow.3",
        "flow.4",
        "flow.1",
        "flow.5",
    ]
    assert refreshed.flow_key == "flow.1"
    assert refreshed.created_at == NOW + timedelta(hours=1)
    assert refreshed.expires_at == NOW + timedelta(hours=25)
    assert [record.flow_key for record in second] == ["flow.1"]


async def test_pending_intents_delete_expired_and_remove_exactly_one(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    user_id, version_id = await _seed_user_and_version(session_factory, uow_factory)
    async with uow_factory() as uow:
        await uow.lock_user(user_id)
        expired = await uow.pending_intents.append(
            _intent(user_id, version_id, "flow.expired", at=NOW - timedelta(days=2)),
            max_per_user=5,
        )
        current = await uow.pending_intents.append(
            _intent(user_id, version_id, "flow.current"), max_per_user=5
        )

    async with uow_factory() as uow:
        await uow.lock_user(user_id)
        assert await uow.pending_intents.delete_expired(user_id, now=NOW) == 1
        await uow.pending_intents.delete(current.id)

    async with uow_factory() as uow:
        remaining = await uow.pending_intents.list_active(user_id, now=NOW)

    assert expired.id != current.id
    assert remaining == []
