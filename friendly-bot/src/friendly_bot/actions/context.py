"""Transaction-scoped, local-only state for one configured action run."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue

from friendly_bot.domain.events import ActionEvent
from friendly_bot.domain.flows import DiscussionFlow
from friendly_bot.domain.state import OpenSelectionState
from friendly_bot.matching.service import MatchingService
from friendly_bot.persistence.repositories import (
    DiagnosticRepository,
    FlowVersionRecord,
    MatchAssignmentRecord,
    NewOutboundDelivery,
    UserRecord,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.services import ServiceAttendanceService, ServiceLifecycleService
from friendly_bot.telegram import TelegramGateway, TelegramMessage

_TEMPLATE_PATTERN = re.compile(r"\{\{([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\}\}")


class ReservedActionEventError(ValueError):
    """Raised when a normal executor attempts to emit the code-owned error event."""


class TerminalActionEventAlreadyEmittedError(RuntimeError):
    """Raised when more than one terminal event is emitted during one action run."""


@dataclass(slots=True)
class ActionContext:
    """Own local rendering, durable output enqueueing, and one terminal event."""

    user: UserRecord
    incoming: TelegramMessage | None
    flow: DiscussionFlow
    flow_version: FlowVersionRecord
    branch: OpenSelectionState
    unit_of_work: UnitOfWork
    now: datetime
    correlation_id: UUID
    local_values: Mapping[str, JsonValue]
    telegram: TelegramGateway
    services: ServiceAttendanceService
    lifecycle: ServiceLifecycleService
    matching: MatchingService
    diagnostics: DiagnosticRepository
    _terminal_event: ActionEvent | None = field(default=None, init=False)
    _delivery_index: int = field(default=0, init=False)
    _match_assignment: MatchAssignmentRecord | None = field(default=None, init=False)

    def render(self, template: str) -> str:
        """Render only the already publication-validated dotted placeholders locally."""

        if not isinstance(template, str):
            raise TypeError("action template must be a string")

        def replace(match: re.Match[str]) -> str:
            return self._render_value(match.group(1))

        rendered = _TEMPLATE_PATTERN.sub(replace, template)
        if "{{" in rendered or "}}" in rendered:
            raise ValueError("action template contains an unresolved placeholder")
        return rendered

    def emit(self, event: ActionEvent) -> None:
        """Save exactly one non-error terminal event for direct-child dispatch."""

        if event.key == "error":
            raise ReservedActionEventError("normal actions cannot emit error")
        if self._terminal_event is not None:
            raise TerminalActionEventAlreadyEmittedError(
                "an action terminal event was already emitted"
            )
        self._terminal_event = event

    async def enqueue_text(self, text: str) -> None:
        """Queue fixed local text through F01's durable outbox, never Telegram directly."""

        if type(text) is not str or not text:
            raise ValueError("outbound text must be nonempty")
        chat_id = self.user.telegram_user_id
        if type(chat_id) is not int or chat_id <= 0:
            raise ValueError("a Telegram user id is required for outbound delivery")
        self._delivery_index += 1
        await self.unit_of_work.deliveries.enqueue(
            NewOutboundDelivery(
                idempotency_key=(
                    f"action:{self.correlation_id}:{self._delivery_index}"
                ),
                user_id=self.user.id,
                telegram_chat_id=chat_id,
                kind="message",
                payload={"text": text},
                eligible_at=self.now,
            )
        )

    def match_request_id(self) -> UUID:
        """Read the local match request identity without exposing it to routing."""

        return self._uuid_value("match.request_id")

    def render_uuid(self, template: str) -> UUID:
        """Render a validated local UUID parameter for an upstream service call."""

        try:
            return UUID(self.render(template))
        except ValueError as error:
            raise ValueError("rendered action value must be a UUID") from error

    def selected_service_id(self) -> UUID:
        """Read the selected service from the local action state."""

        return self._uuid_value("service.id")

    def set_match_assignment(self, assignment: MatchAssignmentRecord | None) -> None:
        """Retain only the typed assignment for subsequent local action executors."""

        self._match_assignment = assignment

    def for_child(
        self,
        child: DiscussionFlow,
        *,
        event_payload: dict[str, JsonValue] | None,
    ) -> ActionContext:
        """Create a fresh terminal-event scope for one direct child only."""

        child_context = replace(
            self,
            flow=child,
            local_values={**self.local_values, **(event_payload or {})},
        )
        child_context._match_assignment = self._match_assignment
        return child_context

    @property
    def match_assignment(self) -> MatchAssignmentRecord | None:
        """Expose the locally retained typed assignment to later executors."""

        return self._match_assignment

    @property
    def terminal_event(self) -> ActionEvent | None:
        """Return the one emitted terminal event, if any."""

        return self._terminal_event

    def _render_value(self, name: str) -> str:
        if name == "user.display_name":
            value: object = self.user.display_name
        else:
            value = self.local_values.get(name)
        if value is None:
            raise ValueError(f"action template value {name!r} is unavailable")
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        raise ValueError(f"action template value {name!r} must be scalar")

    def _uuid_value(self, name: str) -> UUID:
        value = self._render_value(name)
        try:
            return UUID(value)
        except ValueError as error:
            raise ValueError(f"local value {name!r} must be a UUID") from error
