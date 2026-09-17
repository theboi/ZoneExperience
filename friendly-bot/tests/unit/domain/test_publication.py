"""Tests for immutable discussion-flow publication validation."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from hashlib import sha256

import pytest
from fixtures import action_event_flow, valid_system_checkpoint, valid_timestamp_root
from pydantic import ValidationError

from friendly_bot.domain.actions import (
    SendMessageFixedAction,
    parse_action,
)
from friendly_bot.domain.flows import (
    DiscussionFlow,
    MultiIntentMode,
    NextFlowMode,
)
from friendly_bot.domain.publication import (
    FlowPublicationError,
    RootKind,
    TemplateContextSchema,
    canonical_json,
    validate_for_publication,
)
from friendly_bot.domain.templates import template_tokens, urls
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


def test_checkpoint_may_return_without_local_children() -> None:
    """A checkpoint may resume at its owning event or global options."""

    checkpoint = valid_timestamp_root()
    checkpoint.next_flow_mode = NextFlowMode.CHECKPOINT
    checkpoint.return_actions = [
        parse_action({"type": "send_message", "text": "What else can I help with?"})
    ]

    published = validate_for_publication(checkpoint, SCHEMA, RootKind.TIMESTAMP)

    assert published.document["next_flows"] == []
    assert published.document["return_actions"] == [
        {"type": "send_message", "text": "What else can I help with?"}
    ]


def test_non_checkpoint_modes_reject_return_actions() -> None:
    """Only checkpoints can return to their owning event or global options."""

    non_checkpoint = valid_timestamp_root()
    non_checkpoint.return_actions = [
        parse_action({"type": "send_message", "text": "Cannot return here."})
    ]

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


def test_unhandled_error_has_no_configured_default_flow() -> None:
    """I04 owns the hardcoded sender, so publication permits an absent error child."""

    root_without_error = valid_system_checkpoint()
    match = root_without_error.next_flows[0]

    assert all(
        getattr(child.trigger, "event_key", None) != "error"
        for child in match.next_flows
    )
    assert "error" not in match.actions[-1].declared_event_keys
    assert validate_for_publication(root_without_error, SCHEMA, RootKind.SYSTEM)


def test_duplicate_direct_normal_handlers_are_rejected() -> None:
    """Breaks if duplicate direct outcomes pass validation."""

    duplicate_normal_handler = valid_system_checkpoint()
    duplicate_normal_handler.next_flows[0].next_flows.append(
        action_event_flow("system.root.match.found_again", "human_match.found")
    )

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

    with pytest.raises(FlowPublicationError):
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


def test_publication_normalizes_fieldless_subclasses_and_rejects_extra_fields() -> None:
    """Breaks if a subclass can extend the closed persisted flow schema."""

    class FieldlessFlow(DiscussionFlow):
        pass

    class ExtraFieldFlow(DiscussionFlow):
        unpublished_extra: str = "must not persist"

    source = valid_system_checkpoint().model_dump()
    normalized_subclass = FieldlessFlow.model_validate(source)
    extra_field_subclass = ExtraFieldFlow.model_validate(source)

    assert validate_for_publication(normalized_subclass, SCHEMA, RootKind.SYSTEM)
    with pytest.raises(FlowPublicationError):
        validate_for_publication(extra_field_subclass, SCHEMA, RootKind.SYSTEM)


def test_published_definition_defensively_copies_document_and_index() -> None:
    """Breaks if caller mutations can desynchronize published content from its hash."""

    published = validate_for_publication(
        valid_system_checkpoint(), SCHEMA, RootKind.SYSTEM
    )
    document = published.document
    index = published.flow_key_index
    document["key"] = "system.mutated"
    index["system.root.match"] = (99,)

    assert published.document["key"] == "system.root"
    assert published.flow_key_index["system.root.match"] == (0,)
    assert (
        published.content_hash == sha256(canonical_json(published.document)).hexdigest()
    )


@pytest.mark.parametrize(
    ("field", "malformed_value"),
    [
        ("actions", None),
        ("return_actions", iter(())),
        ("next_flows", None),
    ],
)
def test_malformed_post_construction_containers_raise_publication_errors_without_warnings(
    field: str,
    malformed_value: object,
) -> None:
    """Breaks if malformed draft containers leak serializer warnings or TypeError."""

    root = valid_system_checkpoint()
    setattr(root, field, malformed_value)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(FlowPublicationError):
            validate_for_publication(root, SCHEMA, RootKind.SYSTEM)


def test_direct_event_handlers_reject_unsolicited_outcomes() -> None:
    """Breaks if a direct action-event child lacks a declared terminal outcome."""

    root = valid_system_checkpoint()
    root.next_flows[0].next_flows.append(
        action_event_flow("system.root.match.unsolicited", "unemitted.event")
    )

    with pytest.raises(FlowPublicationError):
        validate_for_publication(root, SCHEMA, RootKind.SYSTEM)


def test_template_filters_allow_only_optional() -> None:
    """Breaks if persisted templates can use an unsupported filter."""

    optional = valid_system_checkpoint()
    optional.return_actions[0].text = "Hello {{ user.name | optional }}."
    unsupported = valid_system_checkpoint()
    unsupported.return_actions[0].text = "Hello {{ user.name | fallback }}."

    assert validate_for_publication(optional, SCHEMA, RootKind.SYSTEM)
    with pytest.raises(FlowPublicationError):
        validate_for_publication(unsupported, SCHEMA, RootKind.SYSTEM)


def test_fixed_message_action_and_template_inspection_are_closed_and_ordered() -> None:
    """Breaks if fixed copy cannot coexist with paraphrasable flow copy."""

    action = parse_action(
        {
            "type": "send_message_fixed",
            "text": "Map: https://example.com/zone for {{ user.name }}",
        }
    )

    assert isinstance(action, SendMessageFixedAction)
    assert template_tokens(action.text) == ("user.name",)
    assert urls(action.text) == ("https://example.com/zone",)


def test_flow_defaults_to_interactive_multi_intent_mode() -> None:
    """Breaks if unclassified flow authors accidentally create answer fragments."""

    flow = DiscussionFlow(
        key="system.default_mode",
        next_flow_mode=NextFlowMode.ALLOW_MANY,
    )

    assert flow.multi_intent_mode is MultiIntentMode.INTERACTIVE


@pytest.mark.parametrize(
    "invalid_answer",
    [
        DiscussionFlow(
            key="system.answer.stateful",
            trigger=parse_trigger({"type": "message", "llm_gist": "stateful"}),
            actions=[parse_action({"type": "return_to_nearest_checkpoint"})],
            next_flow_mode=NextFlowMode.ALLOW_MANY,
            multi_intent_mode=MultiIntentMode.ANSWER,
        ),
        DiscussionFlow(
            key="system.answer.with_child",
            trigger=parse_trigger({"type": "message", "llm_gist": "child"}),
            actions=[parse_action({"type": "send_message", "text": "Answer"})],
            next_flows=[
                DiscussionFlow(
                    key="system.answer.with_child.follow_up",
                    trigger=parse_trigger({"type": "message", "llm_gist": "follow up"}),
                    next_flow_mode=NextFlowMode.ALLOW_MANY,
                )
            ],
            next_flow_mode=NextFlowMode.ALLOW_MANY,
            multi_intent_mode=MultiIntentMode.ANSWER,
        ),
    ],
)
def test_publication_rejects_non_fragment_answer_flows(
    invalid_answer: DiscussionFlow,
) -> None:
    """Breaks if an answer flow can mutate or open conversational state."""

    root = valid_system_checkpoint()
    root.next_flows.append(invalid_answer)

    with pytest.raises(FlowPublicationError):
        validate_for_publication(root, SCHEMA, RootKind.SYSTEM)
