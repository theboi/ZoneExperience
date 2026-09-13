"""PostgreSQL coverage for the scheduler's authoritative audience adapter."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import Enum, MetaData
from sqlalchemy.dialects.postgresql import CreateEnumType, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import (
    OperationalRole,
    Service,
    ServiceAttendance,
    ServiceAudience,
    User,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.services.scheduler import AudienceResolver

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
