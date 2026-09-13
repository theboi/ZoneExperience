"""PostgreSQL durability contracts for Telegram polling ingress."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy import Enum, MetaData, select
from sqlalchemy.dialects.postgresql import CreateEnumType, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import (
    ConversationMessage,
    OutboundDelivery,
    ProcessedTelegramUpdate,
)
from friendly_bot.persistence.repositories import NewOutboundDelivery
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.telegram.models import (
    IncomingTelegramUpdate,
    TelegramApiError,
    TelegramApiFailure,
    TelegramChat,
    TelegramMessage,
    TelegramResponseUncertain,
    TelegramUpdates,
    TelegramUser,
)
from friendly_bot.telegram.poller import TelegramIngress, TelegramPoller

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_SESSION_FACTORY = async_sessionmaker[AsyncSession]


class IngressFailure(RuntimeError):
    """A dispatcher failure that must roll back the complete ingress transaction."""


async def _create_schema(
    connection: asyncpg.Connection[asyncpg.Record], schema: str
) -> None:
    """Create isolated F01 tables without adding a local persistence boundary."""

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
    """Provide an isolated PostgreSQL schema for durable polling behaviour."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")
    url = make_url(database_url)
    schema = f"friendly_bot_t02_poller_{uuid4().hex}"
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
    """Create a separate F01 transaction for each poll/update operation."""

    return lambda: UnitOfWork(session_factory)


@dataclass
class RecordingDispatcher:
    """Observe normalized dispatch after real repository work remains in the UoW."""

    dispatched_message_ids: list[int] = field(default_factory=list)
    fail: bool = False
    enqueue_delivery_before_failure: bool = False

    async def dispatch(
        self,
        *,
        user_id: UUID,
        incoming: TelegramMessage,
        unit_of_work: UnitOfWork,
    ) -> None:
        if self.fail:
            if self.enqueue_delivery_before_failure:
                await unit_of_work.deliveries.enqueue(
                    NewOutboundDelivery(
                        idempotency_key=f"poller-failed-dispatch-{incoming.message_id}",
                        user_id=user_id,
                        telegram_chat_id=incoming.chat.id,
                        kind="test.fixed_reply",
                        payload={"template": "fixed"},
                    )
                )
            raise IngressFailure("dispatch failed")
        self.dispatched_message_ids.append(incoming.message_id)


@dataclass
class StaticTelegramGateway:
    """Return one complete typed batch and retain only requested offsets."""

    result: TelegramUpdates | TelegramApiFailure
    requested_offsets: list[int] = field(default_factory=list)

    async def get_updates(
        self, *, offset: int, timeout_seconds: int
    ) -> TelegramUpdates | TelegramApiFailure:
        del timeout_seconds
        self.requested_offsets.append(offset)
        return self.result


def _message_update(
    update_id: int,
    *,
    message_id: int | None = None,
    text: str = "Hello",
    reply_text: str | None = None,
) -> IncomingTelegramUpdate:
    return IncomingTelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            message_id=message_id or update_id,
            sent_at=NOW,
            chat=TelegramChat(id=41, kind="private"),
            sender=TelegramUser(id=73),
            text=text,
            reply_text=reply_text,
            callback_data=None,
        ),
    )


def _callback_update(update_id: int) -> IncomingTelegramUpdate:
    return IncomingTelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            message_id=92,
            sent_at=NOW,
            chat=TelegramChat(id=41, kind="private"),
            sender=TelegramUser(id=73),
            text="Prompt shown to the user",
            reply_text="Earlier question",
            callback_data="zone_x.attendance.here",
        ),
    )


async def _polling_offset(factory: Callable[[], UnitOfWork]) -> int:
    async with factory() as uow:
        return (await uow.poll_state.get()).next_update_offset


async def _conversation_rows(
    session_factory: _SESSION_FACTORY,
) -> list[ConversationMessage]:
    async with session_factory() as session:
        return list(
            await session.scalars(
                select(ConversationMessage).order_by(
                    ConversationMessage.source_message_id
                )
            )
        )


async def _processed_update_count(session_factory: _SESSION_FACTORY) -> int:
    async with session_factory() as session:
        return len(list(await session.scalars(select(ProcessedTelegramUpdate))))


async def _outbound_delivery_count(session_factory: _SESSION_FACTORY) -> int:
    async with session_factory() as session:
        return len(list(await session.scalars(select(OutboundDelivery))))


async def test_committed_duplicate_after_restart_is_a_noop_and_offset_is_monotonic(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Dropping the durable update claim would record a second inbound message."""

    dispatcher = RecordingDispatcher()
    first = TelegramIngress(uow_factory, dispatcher)
    update = _message_update(71)

    await first.process(update, received_at=NOW)
    restarted = TelegramIngress(uow_factory, dispatcher)
    await restarted.process(update, received_at=NOW)

    assert await _polling_offset(uow_factory) == 72
    assert await _processed_update_count(session_factory) == 1
    assert [row.body for row in await _conversation_rows(session_factory)] == ["Hello"]
    assert dispatcher.dispatched_message_ids == [71]


async def test_dispatch_failure_rolls_back_claim_message_delivery_and_cursor(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Committing before dispatch would make a failed update permanently skipped."""

    ingress = TelegramIngress(
        uow_factory,
        RecordingDispatcher(fail=True, enqueue_delivery_before_failure=True),
    )

    with pytest.raises(IngressFailure, match="^dispatch failed$"):
        await ingress.process(_message_update(9), received_at=NOW)

    assert await _polling_offset(uow_factory) == 0
    assert await _processed_update_count(session_factory) == 0
    assert await _conversation_rows(session_factory) == []
    assert await _outbound_delivery_count(session_factory) == 0


async def test_poller_sorts_a_returned_batch_before_dispatching_and_advancing_cursor(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Processing Telegram's returned order could skip an earlier update after restart."""

    dispatcher = RecordingDispatcher()
    gateway = StaticTelegramGateway(
        TelegramUpdates((_message_update(19), _message_update(17)))
    )
    poller = TelegramPoller(
        gateway,
        TelegramIngress(uow_factory, dispatcher),
        uow_factory,
        timeout_seconds=25,
    )

    result = await poller.run_once(now=NOW)

    assert gateway.requested_offsets == [0]
    assert dispatcher.dispatched_message_ids == [17, 19]
    assert result.processed_update_ids == (17, 19)
    assert await _polling_offset(uow_factory) == 20


@pytest.mark.parametrize(
    "failure",
    [
        TelegramApiError(502),
        TelegramResponseUncertain("telegram_response_malformed"),
    ],
)
async def test_poller_gateway_failure_does_not_claim_or_advance(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
    failure: TelegramApiFailure,
) -> None:
    """Treating a failed poll as an empty batch could silently drop Telegram input."""

    gateway = StaticTelegramGateway(failure)
    poller = TelegramPoller(
        gateway,
        TelegramIngress(uow_factory, RecordingDispatcher()),
        uow_factory,
        timeout_seconds=25,
    )

    result = await poller.run_once(now=NOW)

    assert gateway.requested_offsets == [0]
    assert result.requested_offset == 0
    assert result.processed_update_ids == ()
    assert result.gateway_failure == failure
    assert await _polling_offset(uow_factory) == 0
    assert await _processed_update_count(session_factory) == 0
    assert await _conversation_rows(session_factory) == []


async def test_callback_input_persists_normalized_callback_and_reply_without_raw_payload(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Persisting Telegram envelopes would retain unneeded raw user payloads."""

    await TelegramIngress(uow_factory, RecordingDispatcher()).process(
        _callback_update(42), received_at=NOW
    )

    row = (await _conversation_rows(session_factory))[0]
    assert row.body == "zone_x.attendance.here"
    assert row.replied_to_body == "Earlier question"
    assert not hasattr(row, "raw_payload")


async def test_unsupported_update_is_claimed_and_advances_without_dispatching(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Leaving unsupported updates unclaimed would cause an endless polling loop."""

    dispatcher = RecordingDispatcher()
    result = await TelegramIngress(uow_factory, dispatcher).process(
        IncomingTelegramUpdate(update_id=83, message=None), received_at=NOW
    )

    assert result.disposition == "ignored"
    assert dispatcher.dispatched_message_ids == []
    assert await _processed_update_count(session_factory) == 1
    assert await _polling_offset(uow_factory) == 84

    replay = await TelegramIngress(uow_factory, dispatcher).process(
        IncomingTelegramUpdate(update_id=83, message=None), received_at=NOW
    )

    assert replay.disposition == "duplicate"
    assert dispatcher.dispatched_message_ids == []
    assert await _processed_update_count(session_factory) == 1
    assert await _polling_offset(uow_factory) == 84
