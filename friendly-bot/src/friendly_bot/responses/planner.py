"""Deterministic local reply-slot discovery for configured flow execution closures."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from friendly_bot.domain.actions import (
    SendMessageLlmAction,
    SendMessageParaphrasedAction,
)
from friendly_bot.domain.flows import DiscussionFlow
from friendly_bot.domain.templates import template_tokens, urls
from friendly_bot.domain.triggers import (
    DiscussionTrigger,
    OnActionEventTrigger,
    OnAnyOfTrigger,
    OnMessageTrigger,
)
from friendly_bot.routing.contracts import ReplySourceSlot


@dataclass(frozen=True, slots=True)
class ReplySlotBinding:
    """Keep one model-visible source bound to a local configured action address."""

    slot_id: str
    flow_key: str
    action_index: int
    mode: Literal["paraphrased", "llm"]
    source: str


@dataclass(frozen=True, slots=True)
class CandidateResponsePlan:
    """The local and prompt-safe views of LLM-generated candidate copy."""

    reply_slots: tuple[ReplySourceSlot, ...]
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
    """One validated LLM reply selected for a configured action address."""

    flow_key: str
    action_index: int
    text: str


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    """Immutable selected LLM replies addressed only by local flow/action coordinates."""

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
        """Return whether a failed LLM reply must use trusted configured copy."""

        return (flow_key, action_index) in self.fallback_addresses


def message_routing_hints(
    trigger: DiscussionTrigger | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return semantic gists and concrete questions from a message-capable trigger."""

    message_triggers = (
        (trigger,)
        if isinstance(trigger, OnMessageTrigger)
        else (
            tuple(
                child
                for child in trigger.triggers
                if isinstance(child, OnMessageTrigger)
            )
            if isinstance(trigger, OnAnyOfTrigger)
            else ()
        )
    )
    return (
        tuple(
            child.llm_gist for child in message_triggers if child.llm_gist is not None
        ),
        tuple(
            question for child in message_triggers for question in child.possible_qns
        ),
    )


def plan_candidate_responses(
    child: DiscussionFlow, *, checkpoint: DiscussionFlow | None
) -> CandidateResponsePlan:
    """Discover authored messages that may execute before another user input."""

    bindings: list[ReplySlotBinding] = []
    for flow in _immediate_action_event_closure(child):
        for action_index, action in enumerate(flow.actions):
            binding = _reply_slot_binding(
                action,
                flow_key=str(flow.key),
                action_index=action_index,
                slot_id=f"r{len(bindings)}",
            )
            if binding is not None:
                bindings.append(binding)
    if checkpoint is not None and _can_return_before_next_input(child):
        for action_index, action in enumerate(checkpoint.return_actions):
            binding = _reply_slot_binding(
                action,
                flow_key=str(checkpoint.key),
                action_index=action_index,
                slot_id=f"r{len(bindings)}",
            )
            if binding is not None:
                bindings.append(binding)
    slots = tuple(
        ReplySourceSlot(
            slot_id=binding.slot_id,
            mode=binding.mode,
            source=binding.source,
            source_template_tokens=template_tokens(binding.source),
            source_urls=urls(binding.source),
        )
        for binding in bindings
    )
    return CandidateResponsePlan(reply_slots=slots, bindings=tuple(bindings))


def _reply_slot_binding(
    action: object, *, flow_key: str, action_index: int, slot_id: str
) -> ReplySlotBinding | None:
    if isinstance(action, SendMessageParaphrasedAction):
        return ReplySlotBinding(
            slot_id=slot_id,
            flow_key=flow_key,
            action_index=action_index,
            mode="paraphrased",
            source=action.text,
        )
    if isinstance(action, SendMessageLlmAction):
        return ReplySlotBinding(
            slot_id=slot_id,
            flow_key=flow_key,
            action_index=action_index,
            mode="llm",
            source=action.source,
        )
    return None


def _immediate_action_event_closure(flow: DiscussionFlow) -> Iterator[DiscussionFlow]:
    """Yield one flow and descendants reachable only through action events."""

    yield flow
    for child in flow.next_flows:
        if isinstance(child.trigger, OnActionEventTrigger):
            yield from _immediate_action_event_closure(child)


def _can_return_before_next_input(flow: DiscussionFlow) -> bool:
    """Require every reachable child edge to be an immediate action-event edge."""

    return all(
        isinstance(child.trigger, OnActionEventTrigger)
        and _can_return_before_next_input(child)
        for child in flow.next_flows
    )
