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

from friendly_bot.app import DispatchResult
from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import (
    ConversationMessage,
    ProcessedTelegramUpdate,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.telegram.callback import (
    TelegramCallback,
    TelegramCallbackContextKind,
)
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
from friendly_bot.telegram.presentations import (
    TelegramPresentation,
    TelegramTextPresentation,
)
from friendly_bot.telegram.sender import DirectSendResult

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
    events: list[str] = field(default_factory=list)
    fail: bool = False
    presentations: tuple[TelegramPresentation, ...] = ()

    async def dispatch(
        self,
        *,
        user_id: UUID,
        incoming: TelegramMessage,
        unit_of_work: UnitOfWork,
    ) -> DispatchResult:
        self.events.append("dispatch")
        if self.fail:
            raise IngressFailure("dispatch failed")
        self.dispatched_message_ids.append(incoming.message_id)
        return DispatchResult("selected", presentations=self.presentations)


@dataclass
class CommitCheckingSender:
    """Observe direct sends only after the real ingress transaction is durable."""

    uow_factory: Callable[[], UnitOfWork]
    expected_offsets: tuple[int, ...]
    events: list[str] = field(default_factory=list)
    presentations: list[tuple[TelegramPresentation, ...]] = field(default_factory=list)
    fail_first: bool = False

    async def send_all(
        self, presentations: tuple[TelegramPresentation, ...]
    ) -> DirectSendResult:
        async with self.uow_factory() as uow:
            assert (await uow.poll_state.get()).next_update_offset == (
                self.expected_offsets[len(self.presentations)]
            )
        self.events.extend(("offset_advanced", "transaction_committed"))
        self.events.append("telegram_send_started")
        self.presentations.append(presentations)
        if self.fail_first and len(self.presentations) == 1:
            return DirectSendResult(len(presentations), 0, len(presentations))
        return DirectSendResult(len(presentations), len(presentations), 0)


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
            callback=None,
        ),
    )


def _callback_update(
    update_id: int,
    *,
    message_id: int = 92,
    callback: TelegramCallback | None = None,
) -> IncomingTelegramUpdate:
    return IncomingTelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            message_id=message_id,
            sent_at=NOW,
            chat=TelegramChat(id=41, kind="private"),
            sender=TelegramUser(id=73),
            text="Prompt shown to the user",
            reply_text="Earlier question",
            callback=callback or TelegramCallback("zone_x.attendance.here"),
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


async def test_restarted_poller_does_not_repeat_a_committed_update_presentation(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A fresh poller must preserve the ingress transaction's durable boundary."""

    update = _message_update(71)
    dispatcher = RecordingDispatcher(
        presentations=(
            TelegramTextPresentation(41, "Delivered from the claimed update"),
        )
    )
    sender = CommitCheckingSender(uow_factory, expected_offsets=(72,))
    first_gateway = StaticTelegramGateway(TelegramUpdates((update,)))
    first = TelegramPoller(
        first_gateway,
        TelegramIngress(uow_factory, dispatcher, sender),
        uow_factory,
        timeout_seconds=25,
    )

    assert (await first.run_once(now=NOW)).processed_update_ids == (71,)

    restarted_gateway = StaticTelegramGateway(TelegramUpdates((update,)))
    restarted = TelegramPoller(
        restarted_gateway,
        TelegramIngress(uow_factory, dispatcher, sender),
        uow_factory,
        timeout_seconds=25,
    )

    assert (await restarted.run_once(now=NOW)).processed_update_ids == (71,)
    assert first_gateway.requested_offsets == [0]
    assert restarted_gateway.requested_offsets == [72]
    assert await _processed_update_count(session_factory) == 1
    assert dispatcher.dispatched_message_ids == [71]
    assert sender.presentations == [dispatcher.presentations]


async def test_dispatch_failure_rolls_back_claim_message_and_cursor(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Committing before dispatch would make a failed update permanently skipped."""

    ingress = TelegramIngress(
        uow_factory,
        RecordingDispatcher(fail=True),
    )

    with pytest.raises(IngressFailure, match="^dispatch failed$"):
        await ingress.process(_message_update(9), received_at=NOW)

    assert await _polling_offset(uow_factory) == 0
    assert await _processed_update_count(session_factory) == 0
    assert await _conversation_rows(session_factory) == []


async def test_ingress_sends_only_after_its_transaction_commits(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A send before the cursor commits can repeat state after a process interruption."""

    events: list[str] = []
    dispatcher = RecordingDispatcher(
        events=events,
        presentations=(TelegramTextPresentation(41, "Committed reply"),),
    )
    sender = CommitCheckingSender(uow_factory, expected_offsets=(10,), events=events)

    result = await TelegramIngress(uow_factory, dispatcher, sender).process(
        _message_update(9), received_at=NOW
    )

    assert events == [
        "dispatch",
        "offset_advanced",
        "transaction_committed",
        "telegram_send_started",
    ]
    assert result.send_attempted == 1
    assert result.send_failed == 0
    assert await _polling_offset(uow_factory) == 10
    assert await _processed_update_count(session_factory) == 1


async def test_failed_direct_send_does_not_block_the_next_committed_update(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Direct delivery is best effort and must not turn a committed update into a retry."""

    dispatcher = RecordingDispatcher(
        presentations=(TelegramTextPresentation(41, "Best effort reply"),)
    )
    sender = CommitCheckingSender(
        uow_factory, expected_offsets=(11, 12), fail_first=True
    )
    ingress = TelegramIngress(uow_factory, dispatcher, sender)

    first = await ingress.process(_message_update(10), received_at=NOW)
    second = await ingress.process(_message_update(11), received_at=NOW)

    assert first.send_failed == 1
    assert second.disposition == "processed"
    assert second.send_failed == 0
    assert sender.presentations == [dispatcher.presentations, dispatcher.presentations]


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


async def test_distinct_callback_presses_on_one_message_are_both_recorded(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Using the shared message ID would silently collapse a second button press."""

    dispatcher = RecordingDispatcher()
    ingress = TelegramIngress(uow_factory, dispatcher)

    await ingress.process(
        _callback_update(
            51, message_id=92, callback=TelegramCallback("zone_x.attendance.here")
        ),
        received_at=NOW,
    )
    await ingress.process(
        _callback_update(
            52, message_id=92, callback=TelegramCallback("zone_x.attendance.late")
        ),
        received_at=NOW,
    )

    assert {
        (row.source_message_id, row.body)
        for row in await _conversation_rows(session_factory)
    } == {
        (-51, "zone_x.attendance.here"),
        (-52, "zone_x.attendance.late"),
    }
    assert dispatcher.dispatched_message_ids == [92, 92]
    assert await _processed_update_count(session_factory) == 2


async def test_callback_and_message_with_same_numeric_id_are_distinct_events(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """Sharing F01's source key across Telegram ID spaces would lose one event."""

    dispatcher = RecordingDispatcher()
    ingress = TelegramIngress(uow_factory, dispatcher)

    await ingress.process(
        _callback_update(
            100, message_id=92, callback=TelegramCallback("zone_x.attendance.here")
        ),
        received_at=NOW,
    )
    await ingress.process(
        _message_update(101, message_id=100, text="Normal message"),
        received_at=NOW,
    )

    rows = await _conversation_rows(session_factory)
    assert [(row.source_message_id, row.body) for row in rows] == [
        (-100, "zone_x.attendance.here"),
        (100, "Normal message"),
    ]
    assert dispatcher.dispatched_message_ids == [92, 100]
    assert await _processed_update_count(session_factory) == 2


async def test_contextual_callback_persists_only_its_stable_button_id(
    session_factory: _SESSION_FACTORY,
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    """A local match UUID must not become routing or conversation text."""

    request_id = uuid4()
    callback = TelegramCallback(
        "zone_x.connect.not_responding",
        TelegramCallbackContextKind.MATCH_REQUEST,
        request_id,
    )

    await TelegramIngress(uow_factory, RecordingDispatcher()).process(
        _callback_update(53, callback=callback), received_at=NOW
    )

    row = (await _conversation_rows(session_factory))[0]
    assert row.body == "zone_x.connect.not_responding"
    assert str(request_id) not in row.body


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
