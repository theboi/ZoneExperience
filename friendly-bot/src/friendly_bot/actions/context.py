"""Transaction-scoped, local-only state for one configured action run."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal, Protocol
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
    MatchRequestRecord,
    MatchResponderRecord,
    ServiceRecord,
    UserRecord,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.responses.planner import ReplyPlan
from friendly_bot.services import ServiceAttendanceService, ServiceLifecycleService
from friendly_bot.telegram import (
    PresentationBuffer,
    TelegramGateway,
    TelegramInlineButton,
    TelegramMessage,
    TelegramPhotoPresentation,
    TelegramTextPresentation,
)

_TEMPLATE_PATTERN = re.compile(
    r"\{\{\s*([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)"
    r"\s*(?:\|\s*(optional))?\s*\}\}"
)


class ReservedActionEventError(ValueError):
    """Raised when a normal executor attempts to emit the code-owned error event."""


class TerminalActionEventAlreadyEmittedError(RuntimeError):
    """Raised when more than one terminal event is emitted during one action run."""


class OrphanPresentationButtonsError(ValueError):
    """Raised when a textless button action has no preceding visible presentation."""


class MissingPlannedLlmReplyError(RuntimeError):
    """Raised when a source-grounded action has no validated model answer."""


class ActionNavigation(Protocol):
    """Application-owned selection transitions invoked by explicit navigation actions."""

    async def enter_service_checkpoint(
        self, context: ActionContext, *, flow_key: str
    ) -> None:
        """Open the named checkpoint for the selected service."""

    async def enter_selected_service_checkpoint(self, context: ActionContext) -> None:
        """Open the selected service's configured main checkpoint."""

    async def enter_selected_service_latecomer_flow(
        self, context: ActionContext
    ) -> None:
        """Open the selected service's configured latecomer root."""

    async def return_to_nearest_checkpoint(self, context: ActionContext) -> None:
        """Apply F01's branch-local nearest-checkpoint transition."""

    async def return_users_to_system_checkpoint(
        self, context: ActionContext, *, user_ids: frozenset[UUID]
    ) -> None:
        """Restore users affected by a service expiry to their system checkpoint."""


@dataclass(slots=True)
class _PendingPresentation:
    """One not-yet-durable Telegram presentation composed within a flow run."""

    kind: Literal["message", "photo"]
    body: str
    asset_key: str | None = None
    buttons: list[TelegramInlineButton] = field(default_factory=list)


@dataclass(slots=True)
class ActionContext:
    """Own local rendering, in-memory presentations, and one terminal event."""

    user: UserRecord
    incoming: TelegramMessage | None
    flow: DiscussionFlow
    flow_version: FlowVersionRecord
    branch: OpenSelectionState
    unit_of_work: UnitOfWork
    now: datetime
    correlation_id: UUID
    local_values: dict[str, JsonValue]
    telegram: TelegramGateway
    services: ServiceAttendanceService
    lifecycle: ServiceLifecycleService
    matching: MatchingService
    diagnostics: DiagnosticRepository
    navigation: ActionNavigation | None = None
    reply_plan: ReplyPlan = field(default_factory=lambda: ReplyPlan(()))
    presentation_buffer: PresentationBuffer = field(default_factory=PresentationBuffer)
    _terminal_event: ActionEvent | None = field(default=None, init=False)
    _match_assignment: MatchAssignmentRecord | None = field(default=None, init=False)
    _match_request: MatchRequestRecord | None = field(default=None, init=False)
    _matched_responder: MatchResponderRecord | None = field(default=None, init=False)
    _previous_responder: MatchResponderRecord | None = field(default=None, init=False)
    _service_choices: tuple[ServiceRecord, ...] = field(default=(), init=False)
    _pending_presentation: _PendingPresentation | None = field(default=None, init=False)
    _active_flow_key: str | None = field(default=None, init=False)
    _active_action_index: int | None = field(default=None, init=False)
    _recorded_llm_reply_fallbacks: set[tuple[str, int]] = field(
        default_factory=set, init=False
    )

    def render(self, template: str) -> str:
        """Render only the already publication-validated dotted placeholders locally."""

        if not isinstance(template, str):
            raise TypeError("action template must be a string")

        def replace(match: re.Match[str]) -> str:
            return self._render_value(
                match.group(1), optional=match.group(2) == "optional"
            )

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
        """Append one standalone text presentation for post-commit sending."""

        if type(text) is not str or not text:
            raise ValueError("outbound text must be nonempty")
        await self.flush_presentation()
        self.presentation_buffer.append(
            TelegramTextPresentation(self._required_chat_id(), text)
        )

    async def queue_text_presentation(self, text: str) -> None:
        """Begin a text presentation that a following button action may extend."""

        if type(text) is not str or not text:
            raise ValueError("outbound text must be nonempty")
        await self.flush_presentation()
        self._pending_presentation = _PendingPresentation(kind="message", body=text)

    async def queue_photo_presentation(self, *, asset_key: str, caption: str) -> None:
        """Begin a photo presentation that a following button action may extend."""

        if type(asset_key) is not str or not asset_key:
            raise ValueError("outbound photo asset key must be nonempty")
        if type(caption) is not str or not caption:
            raise ValueError("outbound photo caption must be nonempty")
        await self.flush_presentation()
        self._pending_presentation = _PendingPresentation(
            kind="photo", body=caption, asset_key=asset_key
        )

    async def attach_presentation_buttons(
        self, buttons: tuple[TelegramInlineButton, ...]
    ) -> None:
        """Attach inline buttons to the immediately preceding visible presentation."""

        pending = self._pending_presentation
        if pending is None:
            raise OrphanPresentationButtonsError(
                "textless send_buttons requires a preceding text or photo presentation"
            )
        if (
            type(buttons) is not tuple
            or not buttons
            or not all(isinstance(button, TelegramInlineButton) for button in buttons)
        ):
            raise ValueError("presentation buttons must be a nonempty typed tuple")
        pending.buttons.extend(buttons)

    async def flush_presentation(self) -> None:
        """Append the buffered presentation before a non-presentation effect or return."""

        pending = self._pending_presentation
        if pending is None:
            return
        self._pending_presentation = None
        buttons = tuple(pending.buttons)
        if pending.kind == "message":
            self.presentation_buffer.append(
                TelegramTextPresentation(
                    self._required_chat_id(), pending.body, buttons
                )
            )
            return
        assert pending.asset_key is not None
        self.presentation_buffer.append(
            TelegramPhotoPresentation(
                self._required_chat_id(), pending.asset_key, pending.body, buttons
            )
        )

    async def enqueue_text_to(
        self, *, user_id: UUID, telegram_chat_id: int, text: str
    ) -> None:
        """Append a fixed external-recipient presentation for post-commit sending."""

        if not isinstance(user_id, UUID):
            raise TypeError("outbound recipient user id must be a UUID")
        if type(telegram_chat_id) is not int or telegram_chat_id <= 0:
            raise ValueError("outbound recipient Telegram id must be positive")
        if type(text) is not str or not text:
            raise ValueError("outbound text must be nonempty")
        await self.flush_presentation()
        self.presentation_buffer.append(
            TelegramTextPresentation(telegram_chat_id, text)
        )

    def match_request_id(self) -> UUID:
        """Read the local match request identity without exposing it to routing."""

        return self._uuid_value("match.request_id")

    def current_match_request_id(self) -> UUID | None:
        """Return a retained request identity only when this action path owns one."""

        return self._match_request.id if self._match_request is not None else None

    def render_uuid(self, template: str) -> UUID:
        """Render a validated local UUID parameter for an upstream service call."""

        try:
            return UUID(self.render(template))
        except ValueError as error:
            raise ValueError("rendered action value must be a UUID") from error

    def selected_service_id(self) -> UUID:
        """Read the selected service from the local action state."""

        return self._uuid_value("service.id")

    def optional_service_id(self, template: str | None) -> UUID | None:
        """Render an optional configured service UUID without inventing a fallback."""

        if template is None:
            return None
        rendered = self.render(template)
        if not rendered:
            return None
        try:
            return UUID(rendered)
        except ValueError as error:
            raise ValueError("rendered action value must be a UUID") from error

    def set_local_value(self, name: str, value: JsonValue) -> None:
        """Retain one local, publication-declared scalar for later actions only."""

        if type(name) is not str or not name:
            raise ValueError("local value name must be nonempty")
        self.local_values[name] = value

    def set_match_request(self, request: MatchRequestRecord) -> None:
        """Retain a requester-owned request and its opaque local callback identity."""

        self._match_request = request
        self.set_local_value("match.request_id", str(request.id))
        if request.interest is not None:
            self.set_local_value("human_match_request.interest", request.interest)

    def set_matched_responder(self, responder: MatchResponderRecord | None) -> None:
        """Retain only the current typed responder contact for local follow-up actions."""

        self._matched_responder = responder
        if responder is None:
            return
        name = responder.display_name or responder.cg_name or "a friendly human"
        self.set_local_value("matched_server.name", name)
        self.set_local_value("matched_human.name", name)
        if responder.cg_name is not None:
            self.set_local_value("matched_server.cg_name", responder.cg_name)
        if responder.telegram_contact_url is not None:
            self.set_local_value(
                "matched_server.telegram_url", responder.telegram_contact_url
            )
            self.set_local_value(
                "matched_human.telegram_url", responder.telegram_contact_url
            )

    def set_previous_responder(self, responder: MatchResponderRecord | None) -> None:
        """Retain a released contact solely for the explicit rematch notification path."""

        self._previous_responder = responder

    def set_service_choices(self, services: tuple[ServiceRecord, ...]) -> None:
        """Retain current F01 service options for the next typed choice action."""

        self._service_choices = services

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
        child_context._match_request = self._match_request
        child_context._matched_responder = self._matched_responder
        child_context._previous_responder = self._previous_responder
        child_context._service_choices = self._service_choices
        child_context._pending_presentation = self._pending_presentation
        child_context.presentation_buffer = self.presentation_buffer
        return child_context

    def for_action(self, flow_key: str, action_index: int) -> ActionContext:
        """Bind one executor call to its deterministic local reply-plan address."""

        if type(flow_key) is not str or not flow_key or action_index < 0:
            raise ValueError("action address is invalid")
        self._active_flow_key = flow_key
        self._active_action_index = action_index
        return self

    async def planned_message_text(self, text: str) -> str:
        """Return a planned paraphrase or the configured authored-copy fallback."""

        if type(text) is not str:
            raise TypeError("message source must be a string")
        if self._active_flow_key is None or self._active_action_index is None:
            return text
        planned = self.reply_plan.text_for(
            self._active_flow_key, self._active_action_index
        )
        if planned is not None:
            return planned
        address = (self._active_flow_key, self._active_action_index)
        if self.reply_plan.requires_fallback(*address):
            await self._record_llm_reply_fallback(address)
        return text

    async def planned_llm_message_text(self) -> str:
        """Return a validated source-grounded answer without exposing its source."""

        if self._active_flow_key is None or self._active_action_index is None:
            raise MissingPlannedLlmReplyError("source-grounded action has no address")
        planned = self.reply_plan.text_for(
            self._active_flow_key, self._active_action_index
        )
        if planned is None:
            raise MissingPlannedLlmReplyError(
                "source-grounded action has no validated reply"
            )
        return planned

    @property
    def match_assignment(self) -> MatchAssignmentRecord | None:
        """Expose the locally retained typed assignment to later executors."""

        return self._match_assignment

    @property
    def matched_responder(self) -> MatchResponderRecord | None:
        """Return the current owner-checked responder contact, if one was resolved."""

        return self._matched_responder

    @property
    def previous_responder(self) -> MatchResponderRecord | None:
        """Return the released responder used by the next explicit notification action."""

        return self._previous_responder

    @property
    def service_choices(self) -> tuple[ServiceRecord, ...]:
        """Expose only current F01-derived service options for button composition."""

        return self._service_choices

    def require_navigation(self) -> ActionNavigation:
        """Fail closed if an application forgot to compose an explicit transition port."""

        if self.navigation is None:
            raise RuntimeError("action navigation is not configured")
        return self.navigation

    @property
    def terminal_event(self) -> ActionEvent | None:
        """Return the one emitted terminal event, if any."""

        return self._terminal_event

    @property
    def queued_delivery_count(self) -> int:
        """Expose the count of local presentations to runtime composition only."""

        return len(self.presentation_buffer.snapshot())

    def _required_chat_id(self) -> int:
        chat_id = self.user.telegram_user_id
        if type(chat_id) is not int or chat_id <= 0:
            raise ValueError("a Telegram user id is required for a presentation")
        return chat_id

    async def _record_llm_reply_fallback(self, address: tuple[str, int]) -> None:
        if address in self._recorded_llm_reply_fallbacks:
            return
        self._recorded_llm_reply_fallbacks.add(address)
        await self.diagnostics.record(
            correlation_id=self.correlation_id,
            severity="warning",
            safe_summary="planned LLM reply unavailable",
            safe_context={"reason_code": "llm_reply.validation_fallback"},
            at=self.now,
        )

    def _render_value(self, name: str, *, optional: bool = False) -> str:
        if name in {"user.display_name", "user.name"}:
            value: object = self.user.display_name
        else:
            value = self.local_values.get(name)
        if value is None:
            if optional:
                return ""
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
