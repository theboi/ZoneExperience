"""Durable, ordered Telegram long-polling ingress."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from friendly_bot.persistence.uow import UnitOfWork, UnitOfWorkFactory
from friendly_bot.telegram.models import (
    IncomingTelegramUpdate,
    TelegramApiFailure,
    TelegramMessage,
    TelegramUpdates,
)

type ProcessedUpdateDisposition = Literal["processed", "duplicate", "ignored"]


class TelegramPollingGateway(Protocol):
    """The polling subset of the typed Telegram transport boundary."""

    async def get_updates(
        self, *, offset: int, timeout_seconds: int
    ) -> TelegramUpdates | TelegramApiFailure:
        """Read one typed Telegram update batch at the supplied durable offset."""


class TelegramUpdateDispatcher(Protocol):
    """Execute configured application work within the claimed update transaction."""

    async def dispatch(
        self,
        *,
        user_id: UUID,
        incoming: TelegramMessage,
        unit_of_work: UnitOfWork,
    ) -> None:
        """Apply configured dispatch work without committing the supplied UoW."""


@dataclass(frozen=True, slots=True)
class ProcessedUpdate:
    """The durable disposition of one Telegram update."""

    update_id: int
    disposition: ProcessedUpdateDisposition


@dataclass(frozen=True, slots=True)
class PollResult:
    """The observable outcome of one long-poll batch without retaining payloads."""

    requested_offset: int
    processed_update_ids: tuple[int, ...]
    gateway_failure: TelegramApiFailure | None = None


class TelegramIngress:
    """Claim, normalize, dispatch, and cursor-commit one Telegram update."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        dispatcher: TelegramUpdateDispatcher,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._dispatcher = dispatcher

    async def process(
        self,
        update: IncomingTelegramUpdate,
        *,
        received_at: datetime,
    ) -> ProcessedUpdate:
        """Commit one complete update transaction or let every mutation roll back."""

        next_offset = update.update_id + 1
        async with self._unit_of_work_factory() as unit_of_work:
            claimed = await unit_of_work.updates.claim_update(
                update.update_id, received_at=received_at
            )
            if not claimed:
                await unit_of_work.poll_state.advance_monotonically(
                    next_offset, at=received_at
                )
                return ProcessedUpdate(update.update_id, "duplicate")

            message = update.message
            if message is None:
                await unit_of_work.poll_state.advance_monotonically(
                    next_offset, at=received_at
                )
                return ProcessedUpdate(update.update_id, "ignored")

            user = await unit_of_work.users.resolve_telegram_sender(
                message.sender.id, received_at=received_at
            )
            await unit_of_work.lock_user(user.id)
            await unit_of_work.conversations.record_incoming(
                user_id=user.id,
                source_message_id=message.message_id,
                body=_normalized_body(message),
                replied_to_body=message.reply_text,
                occurred_at=message.sent_at,
            )
            await self._dispatcher.dispatch(
                user_id=user.id,
                incoming=message,
                unit_of_work=unit_of_work,
            )
            await unit_of_work.poll_state.advance_monotonically(
                next_offset, at=received_at
            )
            return ProcessedUpdate(update.update_id, "processed")


class TelegramPoller:
    """Fetch caller-owned offsets and process each returned update in ID order."""

    def __init__(
        self,
        gateway: TelegramPollingGateway,
        ingress: TelegramIngress,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        timeout_seconds: int,
    ) -> None:
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("Telegram long-poll timeout must be positive")
        self._gateway = gateway
        self._ingress = ingress
        self._unit_of_work_factory = unit_of_work_factory
        self._timeout_seconds = timeout_seconds

    async def polling_offset(self) -> int:
        """Read the only durable poll cursor without holding its transaction open."""

        async with self._unit_of_work_factory() as unit_of_work:
            return (await unit_of_work.poll_state.get()).next_update_offset

    async def run_once(self, *, now: datetime) -> PollResult:
        """Fetch and transactionally process one batch without advancing on API failure."""

        offset = await self.polling_offset()
        updates = await self._gateway.get_updates(
            offset=offset, timeout_seconds=self._timeout_seconds
        )
        if not isinstance(updates, TelegramUpdates):
            return PollResult(
                requested_offset=offset,
                processed_update_ids=(),
                gateway_failure=updates,
            )

        processed_updates: list[ProcessedUpdate] = []
        for update in sorted(updates.updates, key=lambda item: item.update_id):
            processed_updates.append(
                await self._ingress.process(update, received_at=now)
            )
        return PollResult(
            requested_offset=offset,
            processed_update_ids=tuple(
                processed.update_id for processed in processed_updates
            ),
        )


def _normalized_body(message: TelegramMessage) -> str:
    """Store the incoming user choice, retaining no Telegram envelope."""

    if message.callback_data is not None:
        return message.callback_data
    if message.text is not None:
        return message.text
    raise ValueError("Telegram message has no supported normalized body")
