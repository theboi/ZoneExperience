"""Durable, ordered Telegram long-polling ingress."""

from __future__ import annotations

import asyncio
import logging
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
from friendly_bot.telegram.presentations import TelegramPresentation
from friendly_bot.telegram.sender import DirectSendResult, TelegramPresentationSender

type ProcessedUpdateDisposition = Literal["processed", "duplicate", "ignored"]

LOGGER = logging.getLogger(__name__)


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
    ) -> object:
        """Apply configured dispatch work without committing the supplied UoW."""


@dataclass(frozen=True, slots=True)
class ProcessedUpdate:
    """The durable disposition of one Telegram update."""

    update_id: int
    disposition: ProcessedUpdateDisposition
    send_attempted: int = 0
    send_failed: int = 0


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
        sender: TelegramPresentationSender | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._dispatcher = dispatcher
        self._sender = sender

    async def process(
        self,
        update: IncomingTelegramUpdate,
        *,
        received_at: datetime,
    ) -> ProcessedUpdate:
        """Commit one complete update transaction or let every mutation roll back."""

        next_offset = update.update_id + 1
        presentations: tuple[TelegramPresentation, ...] = ()
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
                source_message_id=_source_event_id(update, message),
                body=_normalized_body(message),
                replied_to_body=message.reply_text,
                occurred_at=message.sent_at,
            )
            result = await self._dispatcher.dispatch(
                user_id=user.id,
                incoming=message,
                unit_of_work=unit_of_work,
            )
            await unit_of_work.poll_state.advance_monotonically(
                next_offset, at=received_at
            )
            presentations = tuple(getattr(result, "presentations", ()))
        delivery = await self._send_after_commit(presentations)
        return ProcessedUpdate(
            update.update_id,
            "processed",
            send_attempted=delivery.attempted,
            send_failed=delivery.failed,
        )

    async def _send_after_commit(
        self, presentations: tuple[TelegramPresentation, ...]
    ) -> DirectSendResult:
        if self._sender is None or not presentations:
            return DirectSendResult(0, 0, 0)
        try:
            return await self._sender.send_all(presentations)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            LOGGER.error("telegram_direct_send outcome=sender_error")
            return DirectSendResult(len(presentations), 0, len(presentations))


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

    if message.callback is not None:
        return message.callback.button_id
    if message.text is not None:
        return message.text
    raise ValueError("Telegram message has no supported normalized body")


def _source_event_id(update: IncomingTelegramUpdate, message: TelegramMessage) -> int:
    """Map Telegram's two positive ID spaces into F01's one signed source key.

    F01 fixes ``source_kind`` to ``telegram``. Normal messages therefore retain
    their positive message IDs while callback events use negative update IDs,
    making the two event identities collision-free in the signed bigint key.
    """

    if message.callback is not None:
        return -update.update_id
    return message.message_id
