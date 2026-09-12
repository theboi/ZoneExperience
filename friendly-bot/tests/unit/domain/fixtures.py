"""Reusable flow definitions for publication-boundary tests."""

from __future__ import annotations

from friendly_bot.domain.actions import parse_action
from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode
from friendly_bot.domain.triggers import parse_trigger


def action_event_flow(key: str, event_key: str) -> DiscussionFlow:
    """Create a leaf that handles one direct action event."""

    return DiscussionFlow(
        key=key,
        trigger=parse_trigger({"type": "action_event", "event_key": event_key}),
        actions=[parse_action({"type": "send_message", "text": "Handled."})],
        next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
    )


def valid_system_checkpoint() -> DiscussionFlow:
    """Create a valid recursive system checkpoint with a no-op leaf."""

    match = DiscussionFlow(
        key="system.root.match",
        trigger=parse_trigger({"type": "button", "button_id": "system.match.start"}),
        actions=[
            parse_action(
                {
                    "type": "find_and_reserve_server",
                    "service_id": "service-1",
                }
            )
        ],
        next_flows=[
            action_event_flow("system.root.match.found", "human_match.found"),
            action_event_flow("system.root.match.not_found", "human_match.not_found"),
        ],
        next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
    )
    no_op_leaf = DiscussionFlow(
        key="system.root.no_op",
        trigger=parse_trigger({"type": "button", "button_id": "system.no_op.open"}),
        next_flow_mode=NextFlowMode.ALLOW_MANY,
    )
    return DiscussionFlow(
        key="system.root",
        actions=[],
        next_flows=[match, no_op_leaf],
        next_flow_mode=NextFlowMode.CHECKPOINT,
        return_actions=[
            parse_action({"type": "send_message", "text": "Hello {{ user.name }}."})
        ],
    )


def valid_timestamp_root() -> DiscussionFlow:
    """Create an automatic timestamp root that is not a checkpoint."""

    return DiscussionFlow(
        key="service.timestamp.notice",
        actions=[parse_action({"type": "send_message", "text": "Reminder."})],
        next_flow_mode=NextFlowMode.ALLOW_MANY,
    )
