"""Tests for immutable discussion-flow publication validation."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from hashlib import sha256

import pytest
from fixtures import action_event_flow, valid_system_checkpoint, valid_timestamp_root
from pydantic import ValidationError

from friendly_bot.domain.actions import parse_action
from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode
from friendly_bot.domain.publication import (
    FlowPublicationError,
    RootKind,
    TemplateContextSchema,
    canonical_json,
    validate_for_publication,
)
from friendly_bot.domain.triggers import parse_trigger

SCHEMA = TemplateContextSchema({"user.name"})


def duplicate_key(root: DiscussionFlow) -> None:
    root.next_flows.append(
        DiscussionFlow(
            key="system.root.match",
            trigger=parse_trigger({"type": "button", "button_id": "system.other.open"}),
            next_flow_mode=NextFlowMode.ALLOW_MANY,
        )
    )


def missing_event_handler(root: DiscussionFlow) -> None:
    root.next_flows[0].next_flows.pop()


def two_error_handlers(root: DiscussionFlow) -> None:
    root.next_flows[0].next_flows.extend(
        [
            action_event_flow("system.root.match.error_one", "error"),
            action_event_flow("system.root.match.error_two", "error"),
        ]
    )


def nonterminal_event_action(root: DiscussionFlow) -> None:
    root.next_flows[0].actions.append(
        parse_action({"type": "send_message", "text": "This must not run."})
    )


@pytest.mark.parametrize(
    "mutator",
    [
        duplicate_key,
        missing_event_handler,
        two_error_handlers,
        nonterminal_event_action,
    ],
)
def test_invalid_recursive_definition_is_not_publishable(
    mutator: Callable[[DiscussionFlow], None],
) -> None:
    """Breaks if recursive keys or terminal direct-event checks are removed."""

    root = valid_system_checkpoint()
    mutator(root)

    with pytest.raises(FlowPublicationError):
        validate_for_publication(root, SCHEMA, RootKind.SYSTEM)


def test_published_definition_is_canonical_hash_stable_and_detached_from_draft() -> (
    None
):
    """Breaks if publication serializes non-canonically or retains mutable draft data."""

    root = valid_system_checkpoint()
    published = validate_for_publication(root, SCHEMA, RootKind.SYSTEM)
    again = validate_for_publication(valid_system_checkpoint(), SCHEMA, RootKind.SYSTEM)
    root.next_flows[0].key = "system.root.changed"

    assert (
        published.content_hash == sha256(canonical_json(published.document)).hexdigest()
    )
    assert published.content_hash == again.content_hash
    assert published.document["next_flows"][0]["key"] == "system.root.match"
    assert published.flow_key_index["system.root.match"] == (0,)


@pytest.mark.parametrize("root_kind", [RootKind.SYSTEM, RootKind.SERVICE])
def test_system_and_service_roots_must_be_triggerless_checkpoints(
    root_kind: RootKind,
) -> None:
    """Breaks if contextual roots are accepted with a trigger or reusable non-checkpoint mode."""

    triggered_root = valid_system_checkpoint()
    triggered_root.trigger = parse_trigger({"type": "automatic"})
    non_checkpoint_root = valid_system_checkpoint()
    non_checkpoint_root.next_flow_mode = NextFlowMode.ALLOW_MANY

    with pytest.raises(FlowPublicationError):
        validate_for_publication(triggered_root, SCHEMA, root_kind)
    with pytest.raises(FlowPublicationError):
        validate_for_publication(non_checkpoint_root, SCHEMA, root_kind)


def test_timestamp_root_is_automatic_and_may_use_a_noncheckpoint_mode() -> None:
    """Breaks if timestamp roots require checkpoints or accept configured triggers."""

    root = valid_timestamp_root()
    published = validate_for_publication(root, SCHEMA, RootKind.TIMESTAMP)
    root.trigger = parse_trigger({"type": "automatic"})

    assert published.document["next_flow_mode"] == "allow_many"
    with pytest.raises(FlowPublicationError):
        validate_for_publication(root, SCHEMA, RootKind.TIMESTAMP)


def test_checkpoint_requires_a_child_and_other_modes_reject_return_actions() -> None:
    """Breaks if checkpoint return semantics become structurally invalid."""

    no_child = valid_system_checkpoint()
    no_child.next_flows = []
    non_checkpoint = valid_timestamp_root()
    non_checkpoint.return_actions = [
        parse_action({"type": "send_message", "text": "Cannot return here."})
    ]

    with pytest.raises(FlowPublicationError):
        validate_for_publication(no_child, SCHEMA, RootKind.SYSTEM)
    with pytest.raises(FlowPublicationError):
        validate_for_publication(non_checkpoint, SCHEMA, RootKind.TIMESTAMP)


def test_event_handlers_must_be_direct_and_exactly_once_for_normal_outcomes() -> None:
    """Breaks if an ancestor or an any-of trigger can satisfy a direct action outcome."""

    root = valid_system_checkpoint()
    match = root.next_flows[0]
    found = match.next_flows[0]
    match.next_flows = [match.next_flows[1]]
    root.next_flows.append(found)

    with pytest.raises(FlowPublicationError):
        validate_for_publication(root, SCHEMA, RootKind.SYSTEM)


def test_one_direct_error_child_is_optional_but_duplicate_normal_handlers_are_rejected() -> (
    None
):
    """Breaks if unhandled errors require configuration or duplicate direct outcomes pass."""

    root_without_error = valid_system_checkpoint()
    duplicate_normal_handler = valid_system_checkpoint()
    duplicate_normal_handler.next_flows[0].next_flows.append(
        action_event_flow("system.root.match.found_again", "human_match.found")
    )

    assert validate_for_publication(root_without_error, SCHEMA, RootKind.SYSTEM)
    with pytest.raises(FlowPublicationError):
        validate_for_publication(duplicate_normal_handler, SCHEMA, RootKind.SYSTEM)


def test_templates_must_be_well_formed_declared_and_string_valued() -> None:
    """Breaks if the publish context permits unknown, malformed, or non-string templates."""

    unknown = valid_system_checkpoint()
    unknown.return_actions[0].text = "Hello {{ matched_human.name }}."
    malformed = valid_system_checkpoint()
    malformed.return_actions[0].text = "Hello {{ user..name }}."
    non_string = valid_system_checkpoint()
    non_string.return_actions[0].text = 42  # type: ignore[assignment]

    for root in (unknown, malformed):
        with pytest.raises(FlowPublicationError):
            validate_for_publication(root, SCHEMA, RootKind.SYSTEM)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(FlowPublicationError):
            validate_for_publication(non_string, SCHEMA, RootKind.SYSTEM)


def test_mutated_button_ids_and_flow_keys_are_rechecked_at_publication() -> None:
    """Breaks if draft mutation can bypass stable key and button validation."""

    invalid_button = valid_system_checkpoint()
    invalid_button.next_flows[1].trigger.button_id = "Invalid Button"  # type: ignore[union-attr]
    invalid_key = valid_system_checkpoint()
    invalid_key.next_flows[1].key = "Invalid Key"

    with pytest.raises(FlowPublicationError):
        validate_for_publication(invalid_button, SCHEMA, RootKind.SYSTEM)
    with pytest.raises(FlowPublicationError):
        validate_for_publication(invalid_key, SCHEMA, RootKind.SYSTEM)


def test_reachable_event_only_cycle_is_rejected() -> None:
    """Breaks if terminal direct-event chains can loop without Telegram input."""

    root = DiscussionFlow(
        key="system.cycle_root",
        actions=[
            parse_action({"type": "find_and_reserve_server", "service_id": "service-1"})
        ],
        next_flow_mode=NextFlowMode.CHECKPOINT,
    )
    first = DiscussionFlow(
        key="system.cycle_first",
        trigger=parse_trigger(
            {"type": "action_event", "event_key": "human_match.found"}
        ),
        actions=[
            parse_action({"type": "find_and_reserve_server", "service_id": "service-1"})
        ],
        next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
    )
    second = DiscussionFlow(
        key="system.cycle_second",
        trigger=parse_trigger(
            {"type": "action_event", "event_key": "human_match.found"}
        ),
        actions=[
            parse_action({"type": "find_and_reserve_server", "service_id": "service-1"})
        ],
        next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
    )
    first.next_flows = [
        second,
        action_event_flow("system.cycle_first.no_match", "human_match.not_found"),
    ]
    second.next_flows = [
        first,
        action_event_flow("system.cycle_second.no_match", "human_match.not_found"),
    ]
    root.next_flows = [
        first,
        action_event_flow("system.cycle_root.no_match", "human_match.not_found"),
    ]

    with pytest.raises(FlowPublicationError, match="event-only cycle"):
        validate_for_publication(root, SCHEMA, RootKind.SYSTEM)


def test_no_op_leaf_warning_is_captured_without_blocking_publication() -> None:
    """Breaks if empty leaves silently disappear or are incorrectly made fatal."""

    published = validate_for_publication(
        valid_system_checkpoint(), SCHEMA, RootKind.SYSTEM
    )

    assert [(warning.code, warning.flow_key) for warning in published.warnings] == [
        ("no_op_leaf", "system.root.no_op")
    ]


def test_recursive_parsing_rejects_unknown_action_and_trigger_discriminators() -> None:
    """Breaks if a persisted nested discriminator can bypass the registered unions."""

    with pytest.raises(ValidationError):
        DiscussionFlow.model_validate(
            {
                "key": "system.unknown",
                "actions": [{"type": "unknown_action"}],
                "next_flow_mode": "checkpoint",
                "next_flows": [
                    {
                        "key": "system.unknown.child",
                        "trigger": {"type": "unknown_trigger"},
                        "next_flow_mode": "allow_many",
                    }
                ],
            }
        )


def test_publication_rejects_an_unknown_action_in_a_bypassed_draft() -> None:
    """Breaks if a malformed in-memory draft escapes as an implementation error."""

    root = valid_system_checkpoint()
    root.next_flows[1].actions = [{"type": "unknown_action"}]  # type: ignore[list-item]

    with pytest.raises(FlowPublicationError):
        validate_for_publication(root, SCHEMA, RootKind.SYSTEM)
