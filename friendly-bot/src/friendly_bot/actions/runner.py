"""Ordered action execution with direct-only terminal-event dispatch."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from friendly_bot.actions.context import ActionContext
from friendly_bot.actions.registry import ActionExecutorRegistry
from friendly_bot.domain.actions import (
    DiscussionAction,
    SendButtonsAction,
    SendMessageAction,
    SendPhotoAction,
    SendServiceChoiceButtonsAction,
)
from friendly_bot.domain.events import ActionEvent
from friendly_bot.domain.flows import DiscussionFlow
from friendly_bot.domain.triggers import ActionEventDiscussionFlowTrigger

DEFAULT_UNHANDLED_ERROR_TEXT = (
    "Sorry, an error occurred. Error log: {telegram_user_id}."
)


class DirectEventHandlerInvariantError(RuntimeError):
    """Raised when a published flow has duplicate direct event children."""


class RetryableActionExecutionError(RuntimeError):
    """Marks a local action failure as safe to retry within the configured bound."""


@dataclass(frozen=True, slots=True)
class ActionRunResult:
    """The flow keys visited while one action path ran."""

    executed_flow_keys: tuple[str, ...]


class ActionRunner:
    """Run actions in declaration order and route one event to one direct child."""

    def __init__(
        self, registry: ActionExecutorRegistry, *, max_safe_attempts: int = 1
    ) -> None:
        if max_safe_attempts < 1:
            raise ValueError("max_safe_attempts must be at least one")
        self._registry = registry
        self._max_safe_attempts = max_safe_attempts

    async def run(
        self, flow: DiscussionFlow, context: ActionContext
    ) -> ActionRunResult:
        executed_flow_keys: list[str] = []
        await self._run_flow(
            flow,
            context,
            executed_flow_keys,
            is_error_recovery=False,
        )
        return ActionRunResult(executed_flow_keys=tuple(executed_flow_keys))

    async def _run_flow(
        self,
        flow: DiscussionFlow,
        context: ActionContext,
        executed_flow_keys: list[str],
        *,
        is_error_recovery: bool,
    ) -> None:
        executed_flow_keys.append(str(flow.key))
        for action in flow.actions:
            if not _is_presentation_action(action):
                await context.flush_presentation()
            event = await self._run_action(action, context, flow)
            if event is None:
                event = context.terminal_event
            if event is None:
                continue
            await context.flush_presentation()
            if is_error_recovery and event.key == "error":
                await send_unhandled_action_error(context)
                return
            await self._run_direct_event_child(
                flow,
                context,
                event,
                executed_flow_keys,
            )
            return
        await context.flush_presentation()

    async def _run_action(
        self,
        action: DiscussionAction,
        context: ActionContext,
        flow: DiscussionFlow,
    ) -> ActionEvent | None:
        executor = self._registry.resolve(action)
        for attempt in range(self._max_safe_attempts):
            try:
                await executor(action, context)
                return None
            except RetryableActionExecutionError:
                if attempt + 1 < self._max_safe_attempts:
                    continue
            except Exception as error:  # noqa: BLE001 - diagnostics must close failures
                del error
            await self._record_action_failure(context, flow, action)
            return ActionEvent(key="error")
        raise AssertionError("action retry loop must return or raise")

    async def _run_direct_event_child(
        self,
        parent: DiscussionFlow,
        context: ActionContext,
        event: ActionEvent,
        executed_flow_keys: list[str],
    ) -> None:
        child = _direct_event_child(parent, event.key)
        if child is None:
            if event.key == "error":
                await send_unhandled_action_error(context)
            else:
                await self._record_unhandled_event(context, parent, event.key)
            return
        await self._run_flow(
            child,
            context.for_child(child, event_payload=event.payload),
            executed_flow_keys,
            is_error_recovery=event.key == "error",
        )

    async def _record_action_failure(
        self,
        context: ActionContext,
        flow: DiscussionFlow,
        action: DiscussionAction,
    ) -> None:
        await self._record_diagnostic(
            context,
            safe_summary="action execution failed",
            safe_context={
                "reason_code": "action_execution.failed",
                "flow_key": str(flow.key),
                "action_type": action.type,
            },
        )

    async def _record_unhandled_event(
        self, context: ActionContext, flow: DiscussionFlow, event_key: str
    ) -> None:
        await self._record_diagnostic(
            context,
            safe_summary="action event has no direct child",
            safe_context={
                "reason_code": "action_event.unhandled_non_error",
                "flow_key": str(flow.key),
                "event_key": event_key,
            },
        )

    async def _record_diagnostic(
        self,
        context: ActionContext,
        *,
        safe_summary: str,
        safe_context: dict[str, JsonValue],
    ) -> None:
        diagnostic = await context.diagnostics.record(
            correlation_id=context.correlation_id,
            severity="error",
            safe_summary=safe_summary,
            safe_context=safe_context,
            at=context.now,
        )
        await context.diagnostics.enqueue_admin_notifications(
            diagnostic.id, at=context.now
        )


def _direct_event_child(
    parent: DiscussionFlow, event_key: str
) -> DiscussionFlow | None:
    matches = [
        child
        for child in parent.next_flows
        if isinstance(child.trigger, ActionEventDiscussionFlowTrigger)
        and child.trigger.event_key == event_key
    ]
    if len(matches) > 1:
        raise DirectEventHandlerInvariantError(
            f"flow {parent.key} has duplicate direct event children for {event_key}"
        )
    return matches[0] if matches else None


async def send_unhandled_action_error(context: ActionContext) -> None:
    """Queue the exact code-owned fallback, never a configurable flow action."""

    await context.enqueue_text(
        DEFAULT_UNHANDLED_ERROR_TEXT.format(
            telegram_user_id=context.user.telegram_user_id
        )
    )


def _is_presentation_action(action: DiscussionAction) -> bool:
    """Only adjacent visible actions may be coalesced into one Telegram request."""

    return isinstance(
        action,
        (
            SendMessageAction,
            SendButtonsAction,
            SendServiceChoiceButtonsAction,
            SendPhotoAction,
        ),
    )
