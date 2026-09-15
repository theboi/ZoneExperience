"""Deterministic reply-slot planning for one typed routing candidate."""

from __future__ import annotations

import pytest

from friendly_bot.domain.actions import SendMessageAction, SendMessageFixedAction
from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode
from friendly_bot.domain.triggers import (
    ActionEventDiscussionFlowTrigger,
    AnyOfDiscussionFlowTrigger,
    ButtonDiscussionFlowTrigger,
    MessageDiscussionFlowTrigger,
)
from friendly_bot.responses.planner import (
    PlannedActionText,
    ReplyPlan,
    message_gists,
    plan_candidate_responses,
)


def test_message_gists_extracts_ordered_message_alternatives_only() -> None:
    trigger = AnyOfDiscussionFlowTrigger(
        type="any_of",
        triggers=[
            ButtonDiscussionFlowTrigger(type="button", button_id="menu.directions"),
            MessageDiscussionFlowTrigger(
                type="message", llm_gist="asks for directions"
            ),
            MessageDiscussionFlowTrigger(type="message", llm_gist="asks for a map"),
        ],
    )

    assert message_gists(trigger) == ("asks for directions", "asks for a map")
    assert (
        message_gists(
            AnyOfDiscussionFlowTrigger(
                type="any_of",
                triggers=[
                    ButtonDiscussionFlowTrigger(
                        type="button", button_id="menu.directions"
                    ),
                    ButtonDiscussionFlowTrigger(type="button", button_id="menu.expect"),
                ],
            )
        )
        == ()
    )


def test_response_slots_cover_immediate_event_paths_and_checkpoint_return() -> None:
    parent = DiscussionFlow(
        key="system.home",
        next_flow_mode=NextFlowMode.CHECKPOINT,
        return_actions=[SendMessageAction(type="send_message", text="anything else?")],
    )
    child = DiscussionFlow(
        key="system.home.ask",
        trigger=MessageDiscussionFlowTrigger(type="message", llm_gist="asks"),
        next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
        actions=[
            SendMessageAction(type="send_message", text="first {{ user.name }}"),
            SendMessageFixedAction(type="send_message_fixed", text="exact copy"),
        ],
        next_flows=[
            DiscussionFlow(
                key="system.home.ask.found",
                trigger=ActionEventDiscussionFlowTrigger(
                    type="action_event", event_key="match.found"
                ),
                next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
                actions=[SendMessageAction(type="send_message", text="second")],
            ),
            DiscussionFlow(
                key="system.home.ask.none",
                trigger=ActionEventDiscussionFlowTrigger(
                    type="action_event", event_key="match.none"
                ),
                next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
                actions=[SendMessageAction(type="send_message", text="third")],
            ),
        ],
    )

    plan = plan_candidate_responses(child, checkpoint=parent)

    assert [
        (slot.slot_id, slot.template, slot.template_tokens) for slot in plan.reply_slots
    ] == [
        ("r0", "first {{ user.name }}", ("user.name",)),
        ("r1", "second", ()),
        ("r2", "third", ()),
        ("r3", "anything else?", ()),
    ]
    assert [(binding.flow_key, binding.action_index) for binding in plan.bindings] == [
        ("system.home.ask", 0),
        ("system.home.ask.found", 0),
        ("system.home.ask.none", 0),
        ("system.home", 0),
    ]


def test_reply_plan_returns_one_text_or_rejects_duplicate_action_addresses() -> None:
    plan = ReplyPlan(messages=(PlannedActionText("flow.one", 0, "planned"),))

    assert plan.text_for("flow.one", 0) == "planned"
    assert plan.text_for("flow.one", 1) is None
    with pytest.raises(ValueError, match="duplicate action address"):
        ReplyPlan(
            messages=(
                PlannedActionText("flow.one", 0, "first"),
                PlannedActionText("flow.one", 0, "second"),
            )
        ).text_for("flow.one", 0)
