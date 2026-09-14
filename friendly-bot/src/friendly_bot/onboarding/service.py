"""Durable onboarding decisions that leave configured flow rendering to callers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from friendly_bot.persistence.uow import UnitOfWork, UnitOfWorkFactory
from friendly_bot.telegram.models import TelegramMessage

type OnboardingOutcomeKind = Literal[
    "name_capture",
    "name_captured",
    "existing_start",
    "existing",
    "ignored",
]


@dataclass(frozen=True, slots=True)
class OnboardingResult:
    """One configured-flow decision without user-facing prose."""

    user_id: UUID
    kind: OnboardingOutcomeKind
    opens_name_capture: bool = False


class OnboardingService:
    """Persist input and decide whether configured onboarding captures a name."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def handle(
        self, message: TelegramMessage, *, now: datetime
    ) -> OnboardingResult:
        """Return the next configured onboarding state in one user-locked transaction."""

        async with self._uow_factory() as uow:
            user = await uow.users.resolve_telegram_sender(
                message.sender.id, received_at=now
            )
            await uow.lock_user(user.id)
            body = _message_body(message)
            await uow.conversations.record_incoming(
                user_id=user.id,
                source_message_id=message.message_id,
                body=body,
                replied_to_body=message.reply_text,
                occurred_at=message.sent_at,
            )
            return await self.handle_in_uow(
                uow, user_id=user.id, message=message, now=now
            )

    async def handle_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        message: TelegramMessage,
        now: datetime,
    ) -> OnboardingResult:
        """Decide onboarding inside ingress's already-locked, recorded transaction."""

        user = await uow.users.require_by_id(user_id)
        if message.text is None:
            return OnboardingResult(user.id, "ignored")
        if user.display_name is not None:
            return OnboardingResult(
                user.id,
                "existing_start" if message.text == "/start" else "existing",
            )
        if message.text == "/start" or not await _is_name_reply(uow, user.id):
            return OnboardingResult(user.id, "name_capture", True)
        await uow.users.set_display_name(user.id, message.text, at=now)
        return OnboardingResult(user.id, "name_captured")


async def _is_name_reply(uow: UnitOfWork, user_id: UUID) -> bool:
    """Use retained input history rather than a process-local onboarding cursor."""

    messages = await uow.conversations.list_after(user_id, None)
    return len(messages) > 1


def _message_body(message: TelegramMessage) -> str:
    """Persist the same normalized input body used by Telegram ingress."""

    if message.text is not None:
        return message.text
    if message.callback_data is not None:
        return message.callback_data
    raise ValueError("Telegram message has no supported normalized body")
