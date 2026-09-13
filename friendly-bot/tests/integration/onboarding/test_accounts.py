"""PostgreSQL contracts for operational account attachment and detachment."""

from __future__ import annotations

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

from friendly_bot.onboarding.accounts import OperationalAccountService
from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import (
    OperationalLogin,
    OperationalProfile,
    OperationalRole,
    User,
)
from friendly_bot.persistence.uow import UnitOfWork

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
DOB = date(1999, 4, 2)
_SESSION_FACTORY = async_sessionmaker[AsyncSession]


async def _create_schema(
    connection: asyncpg.Connection[asyncpg.Record], schema: str
) -> None:
    """Create F01's real metadata in this test's isolated PostgreSQL schema."""

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
    """Supply a disposable real schema when PostgreSQL integration is configured."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")
    url = make_url(database_url)
    schema = f"friendly_bot_task5_onboarding_{uuid4().hex}"
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
    """Open a fresh F01 transaction for each account operation."""

    return lambda: UnitOfWork(session_factory)


async def _seed_profile(
    session_factory: _SESSION_FACTORY,
    *,
    normalized_name: str,
    role: OperationalRole,
    telegram_user_id: int | None = None,
) -> tuple[UUID, UUID]:
    """Create the prefilled shared user and its separate operational profile."""

    user_id = uuid4()
    profile_id = uuid4()
    async with session_factory.begin() as session:
        session.add(
            User(
                id=user_id,
                telegram_user_id=telegram_user_id,
                role=role,
                is_admin=False,
            )
        )
        session.add(
            OperationalProfile(
                id=profile_id,
                user_id=user_id,
                normalized_name=normalized_name,
                dob=DOB,
                interests=["prefilled"],
                cg_name=None,
                telegram_contact_url=None,
                always_available=False,
                capacity=1,
                reserved_capacity=0,
            )
        )
    return user_id, profile_id


async def test_operational_profile_cannot_attach_to_two_telegram_accounts(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Removing F01's conflict-aware attach result would reveal or double-occupy a profile."""

    await _seed_profile(
        session_factory, normalized_name="Jordan", role=OperationalRole.NBNC
    )
    accounts = OperationalAccountService(uow_factory)

    first = await accounts.login(1, "Jordan", DOB, now=NOW)
    second = await accounts.login(2, "Jordan", DOB, now=NOW + timedelta(minutes=1))

    assert first.kind == "attached"
    assert second.kind == "occupied"
    assert second.opens_interest_capture is False


@pytest.mark.parametrize(
    "role", [OperationalRole.SERVER, OperationalRole.LEADER, OperationalRole.STAFF]
)
async def test_first_login_for_every_operational_role_opens_interest_capture(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
    role: OperationalRole,
) -> None:
    """Reading a profile role would skip a valid server, leader, or staff login."""

    telegram_user_id = {
        OperationalRole.SERVER: 11,
        OperationalRole.LEADER: 12,
        OperationalRole.STAFF: 13,
    }[role]
    await _seed_profile(
        session_factory,
        normalized_name=f"{role.value}-name",
        role=role,
        telegram_user_id=telegram_user_id,
    )
    accounts = OperationalAccountService(uow_factory)

    result = await accounts.login(telegram_user_id, f"{role.value}-name", DOB, now=NOW)

    assert result.kind == "attached"
    assert result.opens_interest_capture is True


async def test_logout_then_reattach_does_not_reopen_interest_capture(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A reattached profile already has durable login history, so capture is not first-login work."""

    await _seed_profile(
        session_factory,
        normalized_name="returning-server",
        role=OperationalRole.SERVER,
        telegram_user_id=18,
    )
    accounts = OperationalAccountService(uow_factory)

    first = await accounts.login(18, "returning-server", DOB, now=NOW)
    await accounts.logout(18, now=NOW + timedelta(minutes=1))
    reattached = await accounts.login(
        18, "returning-server", DOB, now=NOW + timedelta(minutes=2)
    )

    assert first.opens_interest_capture is True
    assert reattached.kind == "attached"
    assert reattached.opens_interest_capture is False


async def test_manage_exposes_interest_editor_without_mutating_prefilled_interests(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Writing interests at login or manage would bypass configured capture actions."""

    _, profile_id = await _seed_profile(
        session_factory,
        normalized_name="server-name",
        role=OperationalRole.SERVER,
        telegram_user_id=21,
    )
    accounts = OperationalAccountService(uow_factory)
    await accounts.login(21, "server-name", DOB, now=NOW)

    result = await accounts.manage(21, now=NOW)
    async with session_factory() as session:
        profile = await session.get(OperationalProfile, profile_id)

    assert result.kind == "manage"
    assert result.opens_interest_editor is True
    assert profile is not None
    assert profile.interests == ["prefilled"]


async def test_logout_detaches_without_deleting_profile_or_login_history(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Deleting on logout would erase matching data and the required audit history."""

    user_id, profile_id = await _seed_profile(
        session_factory,
        normalized_name="staff-name",
        role=OperationalRole.STAFF,
        telegram_user_id=31,
    )
    accounts = OperationalAccountService(uow_factory)
    await accounts.login(31, "staff-name", DOB, now=NOW)

    result = await accounts.logout(31, now=NOW + timedelta(minutes=1))
    async with session_factory() as session:
        profile = await session.get(OperationalProfile, profile_id)
        user = await session.get(User, user_id)
        logins = list(
            await session.scalars(
                select(OperationalLogin).where(OperationalLogin.user_id == user_id)
            )
        )

    assert result.kind == "detached"
    assert profile is not None
    assert user is not None
    assert len(logins) == 1
    assert logins[0].detached_at == NOW + timedelta(minutes=1)
