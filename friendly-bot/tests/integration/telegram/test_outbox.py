"""PostgreSQL durability contracts for Telegram outbound delivery."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy import Enum, MetaData, select
from sqlalchemy.dialects.postgresql import CreateEnumType, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import OutboundDelivery, OutboundDeliveryAttempt
from friendly_bot.persistence.repositories import (
    DeliveryRepository,
    NewOutboundDelivery,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.telegram.models import (
    OutboundTelegramMessage,
    TelegramResponseUncertain,
    TelegramSendConfirmed,
    TelegramSendOutcome,
    TelegramSendRejected,
    TelegramSendRetry,
)
from friendly_bot.telegram.outbox import OutboundDeliveryWorker

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_SESSION_FACTORY = async_sessionmaker[AsyncSession]


async def _create_schema(
    connection: asyncpg.Connection[asyncpg.Record], schema: str
) -> None:
    """Create isolated real F01 tables without adding a persistence boundary."""

    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata, schema=schema)
    enum_types: dict[str, Enum] = {}
    for table in metadata.sorted_tables:
        for column in table.columns:
            if isinstance(column.type, Enum) and column.type.native_enum:
                column.type.schema = schema
                enum_types.setdefault(column.type.name or column.name, column.type)
    postgres = dialect()  # type: ignore[no-untyped-call]
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
    """Provide an isolated PostgreSQL schema for actual F01 outbox behaviour."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")
    url = make_url(database_url)
    schema = f"friendly_bot_t02_outbox_{uuid4().hex}"
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
    """Open one separate committed F01 transaction per delivery phase."""

    return lambda: UnitOfWork(session_factory)


@dataclass
class RecordingGateway:
    """A typed direct-gateway substitute with no retained response payloads."""

    outcome: TelegramSendOutcome
    requests: list[OutboundTelegramMessage] = field(default_factory=list)

    async def send(self, request: OutboundTelegramMessage) -> TelegramSendOutcome:
        self.requests.append(request)
        return self.outcome


@dataclass
class SequencedGateway:
    """Return typed outcomes in durable claim order without retaining responses."""

    outcomes: list[TelegramSendOutcome]
    requests: list[OutboundTelegramMessage] = field(default_factory=list)

    async def send(self, request: OutboundTelegramMessage) -> TelegramSendOutcome:
        self.requests.append(request)
        return self.outcomes.pop(0)


class _AfterClaimUnitOfWork:
    """Run one real PostgreSQL interleaving after the worker's claim commits."""

    def __init__(
        self,
        unit_of_work: UnitOfWork,
        after_claim: Callable[[], Awaitable[None]] | None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._after_claim = after_claim

    @property
    def deliveries(self) -> DeliveryRepository:
        return self._unit_of_work.deliveries

    async def __aenter__(self) -> Self:
        await self._unit_of_work.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._unit_of_work.__aexit__(exc_type, exc, traceback)
        if exc_type is None and self._after_claim is not None:
            await self._after_claim()


class _RateLimitInterleavingFactory:
    """Use real F01 UoWs while injecting one finished 429 between worker phases."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        after_claim: Callable[[], Awaitable[None]],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._after_claim = after_claim
        self._first = True

    def __call__(self) -> _AfterClaimUnitOfWork:
        after_claim = self._after_claim if self._first else None
        self._first = False
        return _AfterClaimUnitOfWork(self._unit_of_work_factory(), after_claim)


async def _enqueue(
    factory: Callable[[], UnitOfWork], *, key: str = "outbox:one"
) -> UUID:
    async with factory() as uow:
        user = await uow.users.resolve_telegram_sender(73, received_at=NOW)
        record = await uow.deliveries.enqueue(
            NewOutboundDelivery(
                idempotency_key=key,
                user_id=user.id,
                telegram_chat_id=73,
                kind="message",
                payload={"text": "Welcome"},
                eligible_at=NOW,
            )
        )
    return record.id


async def _delivery(
    session_factory: _SESSION_FACTORY, delivery_id: UUID
) -> OutboundDelivery:
    async with session_factory() as session:
        row = await session.get(OutboundDelivery, delivery_id)
    assert row is not None
    return row


async def _attempts(
    session_factory: _SESSION_FACTORY, delivery_id: UUID
) -> list[OutboundDeliveryAttempt]:
    async with session_factory() as session:
        return list(
            await session.scalars(
                select(OutboundDeliveryAttempt)
                .where(OutboundDeliveryAttempt.delivery_id == delivery_id)
                .order_by(OutboundDeliveryAttempt.attempt_number)
            )
        )


async def _finish_two_retry_attempts(
    factory: Callable[[], UnitOfWork], delivery_id: UUID
) -> None:
    """Make the next worker send a finite-limit terminal rate-limit attempt."""

    for _ in range(2):
        async with factory() as uow:
            claim = await uow.deliveries.claim_next_safe(now=NOW)
            assert claim is not None
            assert claim.id == delivery_id
            assert claim.claim_token is not None
            attempt = await uow.deliveries.start_attempt(
                claim.id,
                uuid4(),
                claim_token=claim.claim_token,
                started_at=NOW,
            )
            await uow.deliveries.finish_attempt(
                claim.id,
                attempt.id,
                "retry",
                now=NOW,
                retry_at=NOW,
                safe_error="telegram_temporary_error",
            )


async def test_committed_attempt_sends_once_and_restart_does_not_replay_sent_work(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A restart after confirmation must not send the same durable delivery again."""

    delivery_id = await _enqueue(uow_factory)
    gateway = RecordingGateway(TelegramSendConfirmed(919))

    assert await OutboundDeliveryWorker(
        uow_factory, gateway, clock=lambda: NOW
    ).run_once()
    assert not await OutboundDeliveryWorker(
        uow_factory, gateway, clock=lambda: NOW + timedelta(minutes=10)
    ).run_once()

    assert gateway.requests == [OutboundTelegramMessage(73, "Welcome", "outbox:one")]
    stored = await _delivery(session_factory, delivery_id)
    assert stored.status == "sent"
    assert stored.confirmed_telegram_message_id == 919
    assert [
        (attempt.attempt_number, attempt.outcome)
        for attempt in await _attempts(session_factory, delivery_id)
    ] == [(1, "sent")]


@pytest.mark.parametrize(
    ("outcome", "status", "retry_at", "safe_error"),
    [
        (
            TelegramSendRetry(503),
            "retry",
            NOW + timedelta(seconds=5),
            "telegram_temporary_error",
        ),
        (
            TelegramSendRetry(429, 11),
            "retry",
            NOW + timedelta(seconds=11),
            "telegram_rate_limited",
        ),
        (TelegramSendRejected(403), "rejected", None, "telegram_rejected"),
        (
            TelegramResponseUncertain("telegram_response_malformed"),
            "uncertain",
            None,
            "telegram_response_malformed",
        ),
    ],
)
async def test_typed_outcome_is_durably_classified_without_ambiguous_replay(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
    outcome: TelegramSendOutcome,
    status: str,
    retry_at: datetime | None,
    safe_error: str,
) -> None:
    """Only typed definite failures become retry candidates in PostgreSQL."""

    delivery_id = await _enqueue(uow_factory, key=f"outbox:{status}")
    gateway = RecordingGateway(outcome)

    assert await OutboundDeliveryWorker(
        uow_factory, gateway, clock=lambda: NOW
    ).run_once()
    if status == "uncertain":
        assert not await OutboundDeliveryWorker(
            uow_factory, gateway, clock=lambda: NOW + timedelta(hours=1)
        ).run_once()

    stored = await _delivery(session_factory, delivery_id)
    assert stored.status == status
    assert stored.eligible_at == (retry_at or NOW)
    attempts = await _attempts(session_factory, delivery_id)
    assert attempts[0].safe_error == safe_error


async def test_rate_limit_pause_blocks_a_second_due_delivery_until_its_exact_expiry(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """An account-wide 429 pause must gate even another ready recipient delivery."""

    first_delivery_id = await _enqueue(uow_factory, key="outbox:rate-limit-first")
    await _finish_two_retry_attempts(uow_factory, first_delivery_id)
    second_delivery_id = await _enqueue(uow_factory, key="outbox:rate-limit-second")
    gateway = SequencedGateway([TelegramSendRetry(429, 60), TelegramSendConfirmed(920)])

    assert await OutboundDeliveryWorker(
        uow_factory, gateway, clock=lambda: NOW
    ).run_once()
    assert (await _delivery(session_factory, first_delivery_id)).status == "rejected"

    assert not await OutboundDeliveryWorker(
        uow_factory, gateway, clock=lambda: NOW + timedelta(seconds=59)
    ).run_once()
    assert gateway.requests == [
        OutboundTelegramMessage(73, "Welcome", "outbox:rate-limit-first")
    ]

    assert await OutboundDeliveryWorker(
        uow_factory, gateway, clock=lambda: NOW + timedelta(seconds=60)
    ).run_once()
    assert gateway.requests == [
        OutboundTelegramMessage(73, "Welcome", "outbox:rate-limit-first"),
        OutboundTelegramMessage(73, "Welcome", "outbox:rate-limit-second"),
    ]
    assert (await _delivery(session_factory, second_delivery_id)).status == "sent"


async def test_rate_limit_between_claim_and_start_fences_the_pre_pause_claim(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A claimed B must not send after A commits a 429 pause before B starts."""

    first_delivery_id = await _enqueue(uow_factory, key="outbox:interleaved-first")
    async with uow_factory() as uow:
        first_claim = await uow.deliveries.claim_next_safe(now=NOW)
    assert first_claim is not None
    assert first_claim.id == first_delivery_id
    assert first_claim.claim_token is not None
    first_claim_token = first_claim.claim_token
    second_delivery_id = await _enqueue(uow_factory, key="outbox:interleaved-second")

    async def finish_first_rate_limited() -> None:
        async with uow_factory() as uow:
            attempt = await uow.deliveries.start_attempt(
                first_claim.id,
                uuid4(),
                claim_token=first_claim_token,
                started_at=NOW,
            )
            await uow.deliveries.finish_attempt(
                first_claim.id,
                attempt.id,
                "retry",
                now=NOW,
                retry_at=NOW + timedelta(seconds=60),
                safe_error="telegram_rate_limited",
            )
            await uow.deliveries.extend_telegram_pause(
                pause_until=NOW + timedelta(seconds=60)
            )

    gateway = RecordingGateway(TelegramSendConfirmed(921))
    worker = OutboundDeliveryWorker(
        _RateLimitInterleavingFactory(uow_factory, finish_first_rate_limited),
        gateway,
        clock=lambda: NOW,
    )

    assert not await worker.run_once()
    assert gateway.requests == []
    assert (await _delivery(session_factory, second_delivery_id)).status == "claimed"
    assert await _attempts(session_factory, second_delivery_id) == []
