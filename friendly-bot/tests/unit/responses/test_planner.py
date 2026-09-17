"""Deterministic reply-slot planning for one typed routing candidate."""

from __future__ import annotations

import pytest

from friendly_bot.domain.actions import (
    SendMessageFixedAction,
    SendMessageLlmAction,
    SendMessageParaphrasedAction,
)
from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode
from friendly_bot.domain.triggers import (
    OnActionEventTrigger,
    OnAnyOfTrigger,
    OnButtonPressTrigger,
    OnMessageTrigger,
)
from friendly_bot.responses.planner import (
    PlannedActionText,
    ReplyPlan,
    message_routing_hints,
    plan_candidate_responses,
)


def test_message_routing_hints_extract_semantic_and_question_alternatives() -> None:
    trigger = OnAnyOfTrigger(
        type="any_of",
        triggers=[
            OnButtonPressTrigger(type="button", button_id="menu.directions"),
            OnMessageTrigger(
                type="message",
                llm_gist="asks for directions",
            ),
            OnMessageTrigger(
                type="message", possible_qns=["Can I get a map?", "Map please"]
            ),
        ],
    )

    assert message_routing_hints(trigger) == (
        ("asks for directions",),
        ("Can I get a map?", "Map please"),
    )
    assert message_routing_hints(
        OnAnyOfTrigger(
            type="any_of",
            triggers=[
                OnButtonPressTrigger(type="button", button_id="menu.directions"),
                OnButtonPressTrigger(type="button", button_id="menu.expect"),
            ],
        )
    ) == ((), ())


def test_response_slots_cover_immediate_event_paths_and_checkpoint_return() -> None:
    parent = DiscussionFlow(
        key="system.home",
        next_flow_mode=NextFlowMode.CHECKPOINT,
        return_actions=[
            SendMessageParaphrasedAction(
                type="send_message_paraphrased", text="anything else?"
            )
        ],
    )
    child = DiscussionFlow(
        key="system.home.ask",
        trigger=OnMessageTrigger(type="message", llm_gist="asks"),
        next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
        actions=[
            SendMessageParaphrasedAction(
                type="send_message_paraphrased", text="first {{ user.name }}"
            ),
            SendMessageFixedAction(type="send_message_fixed", text="exact copy"),
        ],
        next_flows=[
            DiscussionFlow(
                key="system.home.ask.found",
                trigger=OnActionEventTrigger(
                    type="action_event", event_key="match.found"
                ),
                next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
                actions=[
                    SendMessageLlmAction(
                        type="send_message_llm", source="only this source may answer"
                    )
                ],
            ),
            DiscussionFlow(
                key="system.home.ask.none",
                trigger=OnActionEventTrigger(
                    type="action_event", event_key="match.none"
                ),
                next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
                actions=[
                    SendMessageParaphrasedAction(
                        type="send_message_paraphrased", text="third"
                    )
                ],
            ),
        ],
    )

    plan = plan_candidate_responses(child, checkpoint=parent)

    assert [
        (slot.slot_id, slot.mode, slot.source, slot.source_template_tokens)
        for slot in plan.reply_slots
    ] == [
        ("r0", "paraphrased", "first {{ user.name }}", ("user.name",)),
        ("r1", "llm", "only this source may answer", ()),
        ("r2", "paraphrased", "third", ()),
        ("r3", "paraphrased", "anything else?", ()),
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
