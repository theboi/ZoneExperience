"""R03 matching integration seam tests; PostgreSQL race coverage is added with safety."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import Enum, MetaData, select
from sqlalchemy.dialects.postgresql import CreateEnumType, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from friendly_bot.matching.service import MatchingService
from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import (
    CapacityReservation,
    HumanMatchAssignment,
    HumanMatchRequest,
    OperationalProfile,
    OperationalRole,
    Service,
    ServiceAttendance,
    User,
)
from friendly_bot.persistence.repositories import (
    MatchAssignmentRecord,
    MatchCandidateRecord,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.routing.contracts import MatchRankingRequest

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
REQUEST = uuid4()
SERVICE = uuid4()
REQUESTER = uuid4()
type _SESSION_FACTORY = async_sessionmaker[AsyncSession]


async def _create_schema(
    connection: asyncpg.Connection[asyncpg.Record], schema: str
) -> None:
    """Create F01's tracked metadata in a schema isolated to this test."""

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
    """Use a generated PostgreSQL schema that is removed after the race proof."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")
    url = make_url(database_url)
    schema = f"friendly_bot_r03_matching_{uuid4().hex}"
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


class ReservationUow:
    def __init__(self, candidate: MatchCandidateRecord) -> None:
        self.matches = self
        self.candidate = candidate
        self.lock_calls = 0
        self.reserved = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def lock_user(self, user_id: UUID) -> None:
        assert user_id == REQUESTER
        self.lock_calls += 1

    async def list_eligible_normal(
        self, service_id: UUID, request_id: UUID
    ) -> list[MatchCandidateRecord]:
        assert (service_id, request_id) == (SERVICE, REQUEST)
        return [self.candidate]

    async def reserve_ranked(
        self, request_id: UUID, profile_ids: list[UUID], *, now: datetime
    ) -> MatchAssignmentRecord:
        assert (request_id, profile_ids, now) == (
            REQUEST,
            [self.candidate.profile_id],
            NOW,
        )
        self.reserved = True
        return MatchAssignmentRecord(uuid4(), REQUEST, self.candidate.profile_id, NOW)


class AliasRanker:
    async def rank_aliases(self, request: MatchRankingRequest) -> list[str]:
        assert [candidate.alias for candidate in request.candidates] == ["candidate-0"]
        return ["candidate-0"]


async def test_normal_reservation_uses_f01_guarded_repository_under_user_lock() -> None:
    """R03 delegates capacity mutation to the F01 guarded repository call."""

    candidate = MatchCandidateRecord(
        profile_id=uuid4(),
        user_id=uuid4(),
        role=OperationalRole.SERVER,
        interests=("music",),
        cg_name="friendly",
        always_available=False,
        capacity=1,
        reserved_capacity=0,
    )
    uow = ReservationUow(candidate)

    assignment = await MatchingService(
        lambda: uow, AliasRanker(), lambda _: REQUESTER
    ).reserve_normal(REQUEST, SERVICE, now=NOW)

    assert assignment is not None
    assert uow.lock_calls == 1
    assert uow.reserved is True


async def test_one_capacity_slot_has_one_concurrent_winner(
    session_factory: _SESSION_FACTORY,
) -> None:
    """F01's guarded reserve leaves exactly one R03 winner for one live slot."""

    requester_one = uuid4()
    requester_two = uuid4()
    responder_user = uuid4()
    responder_profile = uuid4()
    service_id = uuid4()
    request_one = uuid4()
    request_two = uuid4()
    async with session_factory.begin() as session:
        session.add_all(
            [
                User(id=requester_one, role=OperationalRole.NBNC),
                User(id=requester_two, role=OperationalRole.NBNC),
                User(id=responder_user, role=OperationalRole.SERVER),
                Service(
                    id=service_id,
                    key=f"r03-capacity-{uuid4().hex}",
                    name="capacity",
                    timezone="UTC",
                    doors_open_at=NOW - timedelta(hours=1),
                    doors_close_at=NOW + timedelta(hours=1),
                    service_starts_at=NOW,
                    service_ends_at=NOW + timedelta(hours=2),
                    interaction_ends_at=NOW + timedelta(hours=3),
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OperationalProfile(
                    id=responder_profile,
                    user_id=responder_user,
                    normalized_name=f"r03-{uuid4().hex}",
                    dob=NOW.date(),
                    interests=["music"],
                    cg_name="capacity",
                    capacity=1,
                    reserved_capacity=0,
                ),
                ServiceAttendance(
                    id=uuid4(),
                    service_id=service_id,
                    user_id=responder_user,
                    attendee_kind="server",
                    started_at=NOW,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                HumanMatchRequest(
                    id=request_one,
                    requester_user_id=requester_one,
                    service_id=service_id,
                    kind="normal",
                    status="pending",
                    created_at=NOW,
                ),
                HumanMatchRequest(
                    id=request_two,
                    requester_user_id=requester_two,
                    service_id=service_id,
                    kind="normal",
                    status="pending",
                    created_at=NOW,
                ),
            ]
        )
    requesters = {request_one: requester_one, request_two: requester_two}
    service = MatchingService(
        lambda: UnitOfWork(session_factory), AliasRanker(), requesters.__getitem__
    )

    results = await asyncio.gather(
        service.reserve_normal(request_one, service_id, now=NOW),
        service.reserve_normal(request_two, service_id, now=NOW),
    )

    async with session_factory() as session:
        profile = await session.get(OperationalProfile, responder_profile)
        assignments = list(
            await session.scalars(
                select(HumanMatchAssignment).where(
                    HumanMatchAssignment.responder_profile_id == responder_profile,
                    HumanMatchAssignment.released_at.is_(None),
                )
            )
        )
        reservations = list(
            await session.scalars(
                select(CapacityReservation).where(
                    CapacityReservation.operational_profile_id == responder_profile,
                    CapacityReservation.released_at.is_(None),
                )
            )
        )

    assert sum(result is not None for result in results) == 1
    assert profile is not None
    assert profile.reserved_capacity == 1
    assert len(assignments) == 1
    assert len(reservations) == 1
