"""Cursor-based persona maintenance through F01's durable repositories."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from friendly_bot.hyperparameters import (
    PERSONA_IDLE_AFTER,
    PERSONA_MAX_UNSUMMARIZED_TOKENS,
)
from friendly_bot.persistence.repositories import ConversationMessageRecord
from friendly_bot.persistence.uow import UnitOfWorkFactory
from friendly_bot.routing.contracts import PersonaSummaryRequest
from friendly_bot.routing.openrouter_gateway import GatewayError

_TOKEN_PATTERN = re.compile(r"\S+")


class PersonaGateway(Protocol):
    """The constrained model operation needed for persona maintenance."""

    async def summarize_persona(self, request: PersonaSummaryRequest) -> str: ...


@dataclass(frozen=True, slots=True)
class PersonaMaintenanceResult:
    """The durable persona visible after one maintenance attempt."""

    generated: bool
    persona: str


class PersonaMaintenanceService:
    """Advance a durable cursor only after a nonempty private summary succeeds."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        gateway: PersonaGateway,
        user_name_for: Callable[[UUID], str],
        *,
        max_unsummarized_tokens: int = PERSONA_MAX_UNSUMMARIZED_TOKENS,
    ) -> None:
        if max_unsummarized_tokens < 1:
            raise ValueError("max_unsummarized_tokens must be at least one")
        self._uow_factory = uow_factory
        self._gateway = gateway
        self._user_name_for = user_name_for
        self._max_unsummarized_tokens = max_unsummarized_tokens

    async def maintain(
        self, user_id: UUID, now: datetime
    ) -> PersonaMaintenanceResult:
        """Regenerate only an eligible post-cursor segment under the F01 user lock."""

        async with self._uow_factory() as uow:
            await uow.lock_user(user_id)
            cursor = await uow.personas.get_or_create(user_id)
            messages = await uow.conversations.list_after(
                user_id, cursor.last_message_id
            )
            if not _should_regenerate(
                messages, now, self._max_unsummarized_tokens
            ):
                return PersonaMaintenanceResult(False, cursor.persona)
            request = PersonaSummaryRequest(
                user_name=self._user_name_for(user_id),
                persona=cursor.persona,
                messages=tuple(message.body for message in messages),
            )
            try:
                summary = await self._gateway.summarize_persona(request)
            except GatewayError:
                return PersonaMaintenanceResult(False, cursor.persona)
            if not summary.strip():
                return PersonaMaintenanceResult(False, cursor.persona)
            await uow.personas.advance(
                user_id,
                persona=summary,
                last_message_id=messages[-1].id,
                generated_at=now,
            )
            return PersonaMaintenanceResult(True, summary)


def estimate_tokens(messages: Sequence[ConversationMessageRecord]) -> int:
    """Count non-whitespace text groups deterministically without a model package."""

    return sum(len(_TOKEN_PATTERN.findall(message.body)) for message in messages)


def _should_regenerate(
    messages: Sequence[ConversationMessageRecord], now: datetime, max_tokens: int
) -> bool:
    if not messages:
        return False
    inactive = now - messages[-1].occurred_at >= PERSONA_IDLE_AFTER
    return inactive or estimate_tokens(messages) >= max_tokens
