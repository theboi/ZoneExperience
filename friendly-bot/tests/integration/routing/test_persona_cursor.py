"""Persona cursor integration seam coverage through the F01 UoW surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID, uuid4

from friendly_bot.persistence.repositories import (
    ConversationMessageRecord,
    PersonaCursorRecord,
)
from friendly_bot.persona.service import PersonaMaintenanceService
from friendly_bot.routing.contracts import PersonaSummaryRequest

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
USER = uuid4()


class CursorUow:
    def __init__(self, message: ConversationMessageRecord) -> None:
        self.personas = self
        self.conversations = self
        self.cursor = PersonaCursorRecord(USER, "", None, None)
        self.message = message
        self.advanced = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def lock_user(self, user_id: UUID) -> None:
        assert user_id == USER

    async def get_or_create(self, user_id: UUID) -> PersonaCursorRecord:
        assert user_id == USER
        return self.cursor

    async def list_after(
        self, user_id: UUID, message_id: UUID | None
    ) -> list[ConversationMessageRecord]:
        assert user_id == USER and message_id is None
        return [self.message]

    async def advance(
        self,
        user_id: UUID,
        *,
        persona: str,
        last_message_id: UUID,
        generated_at: datetime,
    ) -> None:
        assert (user_id, last_message_id, generated_at) == (USER, self.message.id, NOW)
        self.cursor = PersonaCursorRecord(USER, persona, last_message_id, generated_at)
        self.advanced = True


class SummaryGateway:
    async def summarize_persona(self, request: PersonaSummaryRequest) -> str:
        assert request.messages == ("fresh",)
        return "warm"


async def test_cursor_advances_only_after_nonempty_summary_through_uow() -> None:
    """The R03 service advances F01's cursor, not a side store, after success."""

    message = ConversationMessageRecord(
        id=uuid4(),
        user_id=USER,
        source_kind="test",
        source_message_id=None,
        body="fresh",
        replied_to_body=None,
        occurred_at=NOW - timedelta(hours=48),
    )
    uow = CursorUow(message)

    result = await PersonaMaintenanceService(
        lambda: uow, SummaryGateway(), lambda _: "A"
    ).maintain(USER, NOW)

    assert result.generated is True
    assert uow.advanced is True
