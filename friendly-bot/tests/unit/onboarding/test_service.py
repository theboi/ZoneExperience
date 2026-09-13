"""Onboarding outcomes preserve user-authored names without rendering flow copy."""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from friendly_bot.onboarding.service import OnboardingService
from friendly_bot.persistence.models import OperationalRole
from friendly_bot.persistence.repositories import ConversationMessageRecord, UserRecord
from friendly_bot.telegram.models import TelegramChat, TelegramMessage, TelegramUser

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


class FakeUow:
    """A durable-shaped UoW double for user and conversation behavior."""

    def __init__(self) -> None:
        self.users = self
        self.conversations = self
        self._users_by_telegram_id: dict[int, UserRecord] = {}
        self.messages: list[ConversationMessageRecord] = []
        self.locked_user_ids: list[UUID] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def resolve_telegram_sender(
        self, telegram_user_id: int, *, received_at: datetime
    ) -> UserRecord:
        del received_at
        user = self._users_by_telegram_id.get(telegram_user_id)
        if user is None:
            user = UserRecord(
                id=uuid4(),
                telegram_user_id=telegram_user_id,
                display_name=None,
                role=OperationalRole.NBNC,
                is_admin=False,
            )
            self._users_by_telegram_id[telegram_user_id] = user
        return user

    async def set_display_name(
        self, user_id: UUID, display_name: str, *, at: datetime
    ) -> UserRecord:
        del at
        for telegram_user_id, user in self._users_by_telegram_id.items():
            if user.id == user_id:
                updated = UserRecord(
                    id=user.id,
                    telegram_user_id=user.telegram_user_id,
                    display_name=display_name,
                    role=user.role,
                    is_admin=user.is_admin,
                )
                self._users_by_telegram_id[telegram_user_id] = updated
                return updated
        raise LookupError("user was not found")

    async def lock_user(self, user_id: UUID) -> None:
        self.locked_user_ids.append(user_id)

    async def record_incoming(
        self,
        *,
        user_id: UUID,
        source_message_id: int,
        body: str,
        replied_to_body: str | None,
        occurred_at: datetime,
    ) -> ConversationMessageRecord:
        record = ConversationMessageRecord(
            id=uuid4(),
            user_id=user_id,
            source_kind="telegram",
            source_message_id=source_message_id,
            body=body,
            replied_to_body=replied_to_body,
            occurred_at=occurred_at,
        )
        self.messages.append(record)
        return record

    async def list_after(
        self, user_id: UUID, message_id: UUID | None
    ) -> list[ConversationMessageRecord]:
        assert message_id is None
        return [message for message in self.messages if message.user_id == user_id]

    def display_name(self, telegram_user_id: int) -> str | None:
        return self._users_by_telegram_id[telegram_user_id].display_name


def message(message_id: int, text: str) -> TelegramMessage:
    """Construct the normalized private update passed to application services."""

    return TelegramMessage(
        message_id=message_id,
        sent_at=NOW,
        chat=TelegramChat(id=81, kind="private"),
        sender=TelegramUser(id=81),
        text=text,
        reply_text=None,
        callback_data=None,
    )


async def test_unknown_start_captures_next_name_exactly() -> None:
    """Trimming a supplied display name would lose an intentional user-authored value."""

    uow = FakeUow()
    onboarding = OnboardingService(lambda: uow)

    start = await onboarding.handle(message(1, "/start"), now=NOW)
    captured = await onboarding.handle(message(2, "Alex  Tan "), now=NOW)

    assert start.kind == "name_capture"
    assert start.opens_name_capture is True
    assert captured.kind == "name_captured"
    assert uow.display_name(81) == "Alex  Tan "


async def test_unknown_ordinary_text_starts_name_capture_before_a_name_is_saved() -> (
    None
):
    """Saving the first unknown message as a name would skip the approved prompt."""

    uow = FakeUow()
    onboarding = OnboardingService(lambda: uow)

    started = await onboarding.handle(message(1, "hello"), now=NOW)
    captured = await onboarding.handle(message(2, "Ari"), now=NOW)

    assert started.kind == "name_capture"
    assert captured.kind == "name_captured"
    assert uow.display_name(81) == "Ari"


async def test_existing_start_returns_configured_welcome_back_outcome() -> None:
    """Replacing an existing name on /start would erase profile history."""

    uow = FakeUow()
    existing = await uow.resolve_telegram_sender(81, received_at=NOW)
    await uow.set_display_name(existing.id, "Existing", at=NOW)
    onboarding = OnboardingService(lambda: uow)

    result = await onboarding.handle(message(1, "/start"), now=NOW)

    assert result.kind == "existing_start"
    assert result.opens_name_capture is False
    assert uow.display_name(81) == "Existing"
