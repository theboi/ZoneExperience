"""Deterministic local reply-slot discovery for configured flow execution closures."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from friendly_bot.domain.actions import SendMessageAction
from friendly_bot.domain.flows import DiscussionFlow
from friendly_bot.domain.templates import template_tokens, urls
from friendly_bot.domain.triggers import (
    ActionEventDiscussionFlowTrigger,
    AnyOfDiscussionFlowTrigger,
    DiscussionFlowTrigger,
    MessageDiscussionFlowTrigger,
)
from friendly_bot.routing.contracts import ReplyTemplateSlot


@dataclass(frozen=True, slots=True)
class ReplySlotBinding:
    """Keep one model-visible slot bound to a local configured action address."""

    slot_id: str
    flow_key: str
    action_index: int
    authored_template: str


@dataclass(frozen=True, slots=True)
class CandidateResponsePlan:
    """The local and prompt-safe views of paraphrasable candidate copy."""

    reply_slots: tuple[ReplyTemplateSlot, ...]
    bindings: tuple[ReplySlotBinding, ...]

    def __post_init__(self) -> None:
        if len(self.reply_slots) != len(self.bindings):
            raise ValueError("reply slots and bindings must have equal length")
        if tuple(slot.slot_id for slot in self.reply_slots) != tuple(
            binding.slot_id for binding in self.bindings
        ):
            raise ValueError("reply slots and bindings must stay in the same order")
        addresses = tuple(
            (binding.flow_key, binding.action_index) for binding in self.bindings
        )
        if len(addresses) != len(set(addresses)):
            raise ValueError("reply slots must not duplicate an action address")


@dataclass(frozen=True, slots=True)
class PlannedActionText:
    """One validated paraphrase selected for a configured action address."""

    flow_key: str
    action_index: int
    text: str


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    """Immutable selected paraphrases addressed only by local flow/action coordinates."""

    messages: tuple[PlannedActionText, ...]
    fallback_addresses: frozenset[tuple[str, int]] = frozenset()

    def __post_init__(self) -> None:
        addresses = tuple(
            (message.flow_key, message.action_index) for message in self.messages
        )
        if len(addresses) != len(set(addresses)):
            raise ValueError("reply plan contains a duplicate action address")
        if set(addresses) & self.fallback_addresses:
            raise ValueError("reply plan cannot plan and fall back for one action")

    def text_for(self, flow_key: str, action_index: int) -> str | None:
        """Return the one planned text for an action, if one was safely validated."""

        matches = tuple(
            message.text
            for message in self.messages
            if message.flow_key == flow_key and message.action_index == action_index
        )
        if len(matches) > 1:
            raise ValueError("reply plan contains a duplicate action address")
        return matches[0] if matches else None

    def requires_fallback(self, flow_key: str, action_index: int) -> bool:
        """Return whether a failed paraphrase must use trusted authored copy."""

        return (flow_key, action_index) in self.fallback_addresses


def message_gists(trigger: DiscussionFlowTrigger | None) -> tuple[str, ...]:
    """Return ordered typed-message gists from a direct or `any_of` trigger."""

    if isinstance(trigger, MessageDiscussionFlowTrigger):
        return (trigger.llm_gist,)
    if not isinstance(trigger, AnyOfDiscussionFlowTrigger):
        return ()
    return tuple(
        child.llm_gist
        for child in trigger.triggers
        if isinstance(child, MessageDiscussionFlowTrigger)
    )


def plan_candidate_responses(
    child: DiscussionFlow, *, checkpoint: DiscussionFlow | None
) -> CandidateResponsePlan:
    """Discover authored messages that may execute before another user input."""

    bindings: list[ReplySlotBinding] = []
    for flow in _immediate_action_event_closure(child):
        for action_index, action in enumerate(flow.actions):
            if not isinstance(action, SendMessageAction):
                continue
            slot_id = f"r{len(bindings)}"
            bindings.append(
                ReplySlotBinding(
                    slot_id=slot_id,
                    flow_key=str(flow.key),
                    action_index=action_index,
                    authored_template=action.text,
                )
            )
    if checkpoint is not None and _can_return_before_next_input(child):
        for action_index, action in enumerate(checkpoint.return_actions):
            if not isinstance(action, SendMessageAction):
                continue
            slot_id = f"r{len(bindings)}"
            bindings.append(
                ReplySlotBinding(
                    slot_id=slot_id,
                    flow_key=str(checkpoint.key),
                    action_index=action_index,
                    authored_template=action.text,
                )
            )
    slots = tuple(
        ReplyTemplateSlot(
            slot_id=binding.slot_id,
            template=binding.authored_template,
            template_tokens=template_tokens(binding.authored_template),
            urls=urls(binding.authored_template),
        )
        for binding in bindings
    )
    return CandidateResponsePlan(reply_slots=slots, bindings=tuple(bindings))


def _immediate_action_event_closure(flow: DiscussionFlow) -> Iterator[DiscussionFlow]:
    """Yield one flow and descendants reachable only through action events."""

    yield flow
    for child in flow.next_flows:
        if isinstance(child.trigger, ActionEventDiscussionFlowTrigger):
            yield from _immediate_action_event_closure(child)


def _can_return_before_next_input(flow: DiscussionFlow) -> bool:
    """Require every reachable child edge to be an immediate action-event edge."""

    return all(
        isinstance(child.trigger, ActionEventDiscussionFlowTrigger)
        and _can_return_before_next_input(child)
        for child in flow.next_flows
    )
