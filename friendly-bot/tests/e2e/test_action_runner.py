"""I04 direct terminal-event dispatch and non-configurable error recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from pydantic import JsonValue

from friendly_bot.actions.context import ActionContext
from friendly_bot.actions.registry import ActionExecutorRegistry
from friendly_bot.actions.runner import ActionRunner
from friendly_bot.domain.actions import (
    FindAndReserveServerAction,
    SendMessageAction,
)
from friendly_bot.domain.events import ActionEvent
from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode
from friendly_bot.domain.triggers import ActionEventDiscussionFlowTrigger
from friendly_bot.persistence.models import OperationalRole
from friendly_bot.persistence.repositories import DiagnosticRecord, UserRecord


@dataclass
class RecordingDiagnostics:
    reason_codes: list[str] = field(default_factory=list)

    async def record(self, **kwargs: object) -> DiagnosticRecord:
        safe_context = cast(dict[str, JsonValue], kwargs["safe_context"])
        self.reason_codes.append(cast(str, safe_context["reason_code"]))
        return DiagnosticRecord(
            id=uuid4(),
            correlation_id=uuid4(),
            severity="error",
            safe_summary="safe",
        )


@dataclass
class RecordingContext:
    user: UserRecord
    diagnostics: RecordingDiagnostics
    texts: list[str]
    now: datetime
    correlation_id: object
    local_values: dict[str, JsonValue] = field(default_factory=dict)
    _terminal_event: ActionEvent | None = None

    def emit(self, event: ActionEvent) -> None:
        self._terminal_event = event

    @property
    def terminal_event(self) -> ActionEvent | None:
        return self._terminal_event

    async def enqueue_text(self, text: str) -> None:
        self.texts.append(text)

    async def flush_presentation(self) -> None:
        return None

    def for_action(self, flow_key: str, action_index: int) -> RecordingContext:
        del flow_key, action_index
        return self

    def for_child(
        self, child: DiscussionFlow, *, event_payload: dict[str, JsonValue] | None
    ) -> RecordingContext:
        del child
        return RecordingContext(
            user=self.user,
            diagnostics=self.diagnostics,
            texts=self.texts,
            now=self.now,
            correlation_id=self.correlation_id,
            local_values={**self.local_values, **(event_payload or {})},
        )


def _context() -> RecordingContext:
    return RecordingContext(
        user=UserRecord(
            id=uuid4(),
            telegram_user_id=77123,
            display_name="Ryan",
            role=OperationalRole.NBNC,
            is_admin=False,
        ),
        diagnostics=RecordingDiagnostics(),
        texts=[],
        now=datetime(2026, 10, 18, 12, 0, tzinfo=UTC),
        correlation_id=uuid4(),
    )


def _flow(
    key: str,
    *,
    actions: list[object] | None = None,
    children: list[DiscussionFlow] | None = None,
    trigger: ActionEventDiscussionFlowTrigger | None = None,
) -> DiscussionFlow:
    return DiscussionFlow.model_validate(
        {
            "key": key,
            "next_flow_mode": NextFlowMode.ONE_AND_ONCE_ONLY,
            "actions": actions or [],
            "next_flows": children or [],
            "trigger": trigger,
        }
    )


def _registry() -> ActionExecutorRegistry:
    registry = ActionExecutorRegistry()

    async def find_server(
        action: FindAndReserveServerAction, context: ActionContext
    ) -> None:
        del action
        cast(RecordingContext, context).emit(ActionEvent(key="human_match.found"))

    async def send_message(action: SendMessageAction, context: ActionContext) -> None:
        if action.text == "raise":
            raise RuntimeError("raw provider body must not escape")
        await cast(RecordingContext, context).enqueue_text(action.text)

    registry.register(FindAndReserveServerAction, find_server)
    registry.register(SendMessageAction, send_message)
    return registry


async def test_terminal_event_executes_one_matching_direct_child_and_stops_parent() -> (
    None
):
    child = _flow(
        "system.parent.found",
        actions=[{"type": "send_message", "text": "child copy"}],
        trigger=ActionEventDiscussionFlowTrigger(
            type="action_event", event_key="human_match.found"
        ),
    )
    parent = _flow(
        "system.parent",
        actions=[
            {
                "type": "find_and_reserve_server",
                "service_id": "30375598-1898-4d46-a1d8-2068453944e4",
            },
            {"type": "send_message", "text": "later parent copy"},
        ],
        children=[child],
    )
    context = _context()

    result = await ActionRunner(_registry()).run(parent, cast(ActionContext, context))

    assert result.executed_flow_keys == ("system.parent", "system.parent.found")
    assert context.texts == ["child copy"]


async def test_event_never_bubbles_to_an_ancestor_or_reusable_past_selection() -> None:
    ancestor = _flow(
        "system.ancestor",
        children=[
            _flow(
                "system.ancestor.found",
                actions=[{"type": "send_message", "text": "must not run"}],
                trigger=ActionEventDiscussionFlowTrigger(
                    type="action_event", event_key="human_match.found"
                ),
            )
        ],
    )
    parent = _flow(
        "system.parent",
        actions=[
            {
                "type": "find_and_reserve_server",
                "service_id": "30375598-1898-4d46-a1d8-2068453944e4",
            }
        ],
    )
    context = _context()

    await ActionRunner(_registry()).run(parent, cast(ActionContext, context))

    assert ancestor.next_flows[0].key == "system.ancestor.found"
    assert context.texts == []
    assert context.diagnostics.reason_codes == ["action_event.unhandled_non_error"]


async def test_direct_error_child_wins_but_unhandled_error_uses_exact_local_sender() -> (
    None
):
    direct_error_parent = _flow(
        "system.direct-error",
        actions=[{"type": "send_message", "text": "raise"}],
        children=[
            _flow(
                "system.direct-error.recovery",
                actions=[{"type": "send_message", "text": "custom recovery"}],
                trigger=ActionEventDiscussionFlowTrigger(
                    type="action_event", event_key="error"
                ),
            )
        ],
    )
    unhandled_error_parent = _flow(
        "system.unhandled-error",
        actions=[{"type": "send_message", "text": "raise"}],
    )
    direct_context = _context()
    runner = ActionRunner(_registry())

    await runner.run(direct_error_parent, cast(ActionContext, direct_context))
    unhandled_context = RecordingContext(
        user=direct_context.user,
        diagnostics=direct_context.diagnostics,
        texts=direct_context.texts,
        now=direct_context.now,
        correlation_id=direct_context.correlation_id,
    )
    await runner.run(unhandled_error_parent, cast(ActionContext, unhandled_context))

    assert direct_context.texts == [
        "custom recovery",
        (f"Sorry, an error occurred. Error log: {direct_context.correlation_id}."),
    ]
    assert direct_context.diagnostics.reason_codes == [
        "action_execution.failed",
        "action_execution.failed",
    ]
