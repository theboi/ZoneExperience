"""Cursor-safe persona maintenance behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from friendly_bot.persistence.repositories import (
    ConversationMessageRecord,
    PersonaCursorRecord,
)
from friendly_bot.persona.service import PersonaMaintenanceService
from friendly_bot.routing.contracts import PersonaSummaryRequest
from friendly_bot.routing.openrouter_gateway import GatewayTransportError

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
USER = uuid4()


@dataclass
class SummaryGateway:
    summary: str | Exception
    requests: list[PersonaSummaryRequest]

    def __init__(self, summary: str | Exception) -> None:
        self.summary = summary
        self.requests = []

    async def summarize_persona(self, request: PersonaSummaryRequest) -> str:
        self.requests.append(request)
        if isinstance(self.summary, Exception):
            raise self.summary
        return self.summary


class FakeUow:
    def __init__(
        self, cursor: PersonaCursorRecord, messages: list[ConversationMessageRecord]
    ) -> None:
        self.personas = self
        self.conversations = self
        self.cursor = cursor
        self.messages = messages
        self.advance_calls: list[tuple[str, UUID, datetime]] = []
        self.locked_user_ids: list[UUID] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def lock_user(self, user_id: UUID) -> None:
        self.locked_user_ids.append(user_id)

    async def get_or_create(self, user_id: UUID) -> PersonaCursorRecord:
        assert user_id == USER
        return self.cursor

    async def list_after(
        self, user_id: UUID, message_id: UUID | None
    ) -> list[ConversationMessageRecord]:
        assert user_id == USER and message_id == self.cursor.last_message_id
        return self.messages

    async def advance(
        self,
        user_id: UUID,
        *,
        persona: str,
        last_message_id: UUID,
        generated_at: datetime,
    ) -> None:
        assert user_id == USER
        self.advance_calls.append((persona, last_message_id, generated_at))
        self.cursor = PersonaCursorRecord(USER, persona, last_message_id, generated_at)


def _message(body: str, *, at: datetime) -> ConversationMessageRecord:
    return ConversationMessageRecord(
        id=uuid4(),
        user_id=USER,
        source_kind="test",
        source_message_id=None,
        body=body,
        replied_to_body=None,
        occurred_at=at,
    )


async def test_success_advances_cursor_without_deleting_messages() -> None:
    """A successful summary must retain source rows and advance to their newest id."""

    old_cursor = uuid4()
    messages = [
        _message("first", at=NOW - timedelta(hours=49)),
        _message("last", at=NOW - timedelta(hours=48)),
    ]
    original_messages = list(messages)
    uow = FakeUow(
        PersonaCursorRecord(USER, "old", old_cursor, NOW - timedelta(days=4)), messages
    )
    service = PersonaMaintenanceService(
        lambda: uow, SummaryGateway("updated"), lambda _: "A"
    )

    result = await service.maintain(USER, NOW)

    assert result.generated is True
    assert uow.cursor.last_message_id == messages[-1].id
    assert uow.messages == original_messages
    assert uow.locked_user_ids == [USER]


async def test_gateway_failure_preserves_old_cursor() -> None:
    """Transport failure must never overwrite a durable persona or cursor."""

    old_cursor = uuid4()
    uow = FakeUow(
        PersonaCursorRecord(USER, "old", old_cursor, NOW - timedelta(days=4)),
        [_message("later", at=NOW - timedelta(hours=48))],
    )
    service = PersonaMaintenanceService(
        lambda: uow,
        SummaryGateway(GatewayTransportError("unavailable")),
        lambda _: "A",
    )

    result = await service.maintain(USER, NOW)

    assert result.generated is False
    assert uow.cursor == PersonaCursorRecord(
        USER, "old", old_cursor, NOW - timedelta(days=4)
    )
    assert uow.advance_calls == []


async def test_token_threshold_generates_without_48_hour_inactivity() -> None:
    """A growing unsummarized segment must not wait indefinitely for inactivity."""

    message = _message("one two three", at=NOW - timedelta(minutes=1))
    uow = FakeUow(PersonaCursorRecord(USER, "", None, None), [message])
    service = PersonaMaintenanceService(
        lambda: uow,
        SummaryGateway("updated"),
        lambda _: "A",
        max_unsummarized_tokens=3,
    )

    result = await service.maintain(USER, NOW)

    assert result.generated is True
    assert uow.cursor.last_message_id == message.id


def test_persona_prompt_dto_forbids_structured_identity_fields() -> None:
    """Summary prompts accept authored text but reject structured account fields."""

    with pytest.raises(ValidationError):
        PersonaSummaryRequest(
            user_name="A",
            persona="old",
            messages=["telegram and dob are ordinary words"],
            telegram_user_id="telegram-id-sentinel-729",
        )
