"""Tests for pure discussion-flow selection transitions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from friendly_bot.domain.actions import DiscussionAction, parse_action
from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode
from friendly_bot.domain.state import (
    DuplicateFlowExecutionError,
    OpenSelectionState,
    SelectionTransitionEngine,
)
from friendly_bot.domain.triggers import parse_trigger

NOW = datetime(2026, 10, 18, 6, 30, tzinfo=UTC)


def flow(
    key: str,
    *,
    mode: NextFlowMode,
    children: list[DiscussionFlow] | None = None,
    actions: list[DiscussionAction] | None = None,
    return_actions: list[DiscussionAction] | None = None,
) -> DiscussionFlow:
    """Build a small flow definition without invoking any runtime executor."""

    return DiscussionFlow(
        key=key,
        actions=actions or [],
        next_flows=children or [],
        next_flow_mode=mode,
        return_actions=return_actions or [],
    )


def button_child(
    key: str,
    *,
    mode: NextFlowMode,
    children: list[DiscussionFlow] | None = None,
    actions: list[DiscussionAction] | None = None,
) -> DiscussionFlow:
    """Build a selectable child with a stable, transport-neutral button trigger."""

    return DiscussionFlow(
        key=key,
        trigger=parse_trigger({"type": "button", "button_id": key}),
        actions=actions or [],
        next_flows=children or [],
        next_flow_mode=mode,
    )


def state(
    key: str,
    *,
    is_current: bool = True,
    service_id: UUID | None = None,
    ancestors: list[str] | None = None,
    checkpoints: list[str] | None = None,
) -> OpenSelectionState:
    """Build an open branch selection with literal lineage for assertions."""

    return OpenSelectionState(
        id=uuid4(),
        user_id=uuid4(),
        flow_version_id=uuid4(),
        parent_flow_key=key,
        service_id=service_id,
        is_current=is_current,
        is_global_interruptive=False,
        ancestor_flow_keys=tuple(ancestors or [key]),
        checkpoint_flow_keys=tuple(checkpoints or []),
        opened_at=NOW,
        last_focused_at=NOW,
    )


@pytest.mark.parametrize(
    ("mode", "expect_deleted", "expect_reusable"),
    [
        (NextFlowMode.ONE_AND_ONCE_ONLY, True, False),
        (NextFlowMode.ALLOW_MANY, False, True),
        (NextFlowMode.CHECKPOINT, False, True),
    ],
)
def test_current_parent_transition_obeys_its_reuse_mode(
    mode: NextFlowMode,
    expect_deleted: bool,
    expect_reusable: bool,
) -> None:
    """Breaks if a current parent is deleted or retained under the wrong mode."""

    grandchild = button_child(
        "system.home.directions.map", mode=NextFlowMode.ONE_AND_ONCE_ONLY
    )
    child = button_child(
        "system.home.directions",
        mode=NextFlowMode.ONE_AND_ONCE_ONLY,
        children=[grandchild],
    )
    parent_definition = flow("system.home", mode=mode, children=[child])
    parent = state(
        "system.home",
        checkpoints=["system.home"] if mode is NextFlowMode.CHECKPOINT else [],
    )

    transition = SelectionTransitionEngine(executed_flow_keys=set()).select_child(
        parent=parent,
        child=child,
        parent_definition=parent_definition,
        now=NOW,
    )

    assert (parent.id in transition.delete_selection_ids) is expect_deleted
    assert (parent.id in transition.reusable_past_selection_ids) is expect_reusable
    assert transition.current_selection_ids == frozenset({"system.home.directions"})


def test_one_and_once_parent_is_deleted_even_if_selected_from_past() -> None:
    """Breaks if an already-past one-time parent can be reused after selection."""

    grandchild = button_child("system.once.answer.next", mode=NextFlowMode.ALLOW_MANY)
    child = button_child(
        "system.once.answer", mode=NextFlowMode.ALLOW_MANY, children=[grandchild]
    )
    parent_definition = flow(
        "system.once", mode=NextFlowMode.ONE_AND_ONCE_ONLY, children=[child]
    )
    parent = state("system.once", is_current=False)

    transition = SelectionTransitionEngine(executed_flow_keys=set()).select_child(
        parent=parent,
        child=child,
        parent_definition=parent_definition,
        now=NOW,
    )

    assert transition.delete_selection_ids == frozenset({parent.id})
    assert transition.reusable_past_selection_ids == frozenset()


def test_reusable_parent_stays_past_when_selected_again() -> None:
    """Breaks if a past ALLOW_MANY parent is demoted or deleted on reuse."""

    grandchild = button_child("system.menu.help.next", mode=NextFlowMode.ALLOW_MANY)
    child = button_child(
        "system.menu.help", mode=NextFlowMode.ALLOW_MANY, children=[grandchild]
    )
    parent_definition = flow(
        "system.menu", mode=NextFlowMode.ALLOW_MANY, children=[child]
    )
    parent = state("system.menu", is_current=False)

    transition = SelectionTransitionEngine(executed_flow_keys=set()).select_child(
        parent=parent,
        child=child,
        parent_definition=parent_definition,
        now=NOW,
    )

    assert transition.delete_selection_ids == frozenset()
    assert transition.reusable_past_selection_ids == frozenset()
    assert transition.current_selection_ids == frozenset({"system.menu.help"})


def test_child_execution_is_deduplicated_within_one_update_only() -> None:
    """Breaks if duplicate processing can execute a child twice in one update."""

    grandchild = button_child("system.menu.help.next", mode=NextFlowMode.ALLOW_MANY)
    child = button_child(
        "system.menu.help", mode=NextFlowMode.ALLOW_MANY, children=[grandchild]
    )
    parent_definition = flow(
        "system.menu", mode=NextFlowMode.ALLOW_MANY, children=[child]
    )
    parent = state("system.menu")
    update_execution_keys: set[str] = set()
    engine = SelectionTransitionEngine(executed_flow_keys=update_execution_keys)

    engine.select_child(
        parent=parent,
        child=child,
        parent_definition=parent_definition,
        now=NOW,
    )

    with pytest.raises(DuplicateFlowExecutionError):
        engine.select_child(
            parent=parent,
            child=child,
            parent_definition=parent_definition,
            now=NOW,
        )

    SelectionTransitionEngine(executed_flow_keys=set()).select_child(
        parent=parent,
        child=child,
        parent_definition=parent_definition,
        now=NOW,
    )


def test_checkpoint_child_selection_preserves_ancestor_and_checkpoint_lineage() -> None:
    """Breaks if checkpoint ancestry is omitted from a newly opened child branch."""

    grandchild = button_child(
        "service.zone_x.questions.toilet", mode=NextFlowMode.ALLOW_MANY
    )
    child = button_child(
        "service.zone_x.questions",
        mode=NextFlowMode.CHECKPOINT,
        children=[grandchild],
    )
    parent_definition = flow(
        "service.zone_x.home",
        mode=NextFlowMode.CHECKPOINT,
        children=[child],
    )
    parent = state(
        "service.zone_x.home",
        service_id=uuid4(),
        ancestors=["system.global", "service.zone_x.home"],
        checkpoints=["system.global"],
    )
    system = flow("system.global", mode=NextFlowMode.CHECKPOINT)

    transition = SelectionTransitionEngine(
        executed_flow_keys=set(),
        flow_definitions={system.key: system},
    ).select_child(
        parent=parent,
        child=child,
        parent_definition=parent_definition,
        now=NOW,
    )

    child_selection = transition.upsert_selections[0]
    assert child_selection.ancestor_flow_keys == (
        "system.global",
        "service.zone_x.home",
        "service.zone_x.questions",
    )
    assert child_selection.checkpoint_flow_keys == (
        "system.global",
        "service.zone_x.home",
        "service.zone_x.questions",
    )


def test_leaf_returns_only_its_nearest_nested_checkpoint() -> None:
    """Breaks if a leaf return crosses into an unrelated current branch."""

    system = flow(
        "system.global",
        mode=NextFlowMode.CHECKPOINT,
        return_actions=[parse_action({"type": "send_message", "text": "System"})],
    )
    service_home = flow(
        "service.zone_x.home",
        mode=NextFlowMode.CHECKPOINT,
        return_actions=[parse_action({"type": "send_message", "text": "Home"})],
    )
    questions = flow(
        "service.zone_x.questions",
        mode=NextFlowMode.CHECKPOINT,
        return_actions=[
            parse_action({"type": "send_message", "text": "Ask a question"})
        ],
    )
    nested_branch = state(
        "service.zone_x.capture_interest",
        service_id=uuid4(),
        ancestors=[
            "system.global",
            "service.zone_x.home",
            "service.zone_x.questions",
            "service.zone_x.capture_interest",
        ],
        checkpoints=[
            "system.global",
            "service.zone_x.home",
            "service.zone_x.questions",
        ],
    )
    engine = SelectionTransitionEngine(
        executed_flow_keys=set(),
        flow_definitions={
            system.key: system,
            service_home.key: service_home,
            questions.key: questions,
        },
    )

    transition = engine.return_to_nearest_checkpoint(branch=nested_branch, now=NOW)

    assert transition.target_checkpoint_key == "service.zone_x.questions"
    assert transition.current_selection_ids == frozenset({"service.zone_x.questions"})
    assert "unrelated.timestamp" not in transition.current_selection_ids
    assert [action.type for action in transition.return_actions] == ["send_message"]


def test_plain_leaf_returns_to_its_checkpoint_after_its_own_actions() -> None:
    """Breaks if an ordinary leaf omits the generic checkpoint return."""

    leaf = button_child(
        "system.home.directions",
        mode=NextFlowMode.ONE_AND_ONCE_ONLY,
        actions=[parse_action({"type": "send_message", "text": "Directions"})],
    )
    parent_definition = flow(
        "system.home",
        mode=NextFlowMode.CHECKPOINT,
        children=[leaf],
        return_actions=[parse_action({"type": "send_message", "text": "Home"})],
    )
    parent = state("system.home", checkpoints=["system.home"])

    transition = SelectionTransitionEngine(executed_flow_keys=set()).select_child(
        parent=parent,
        child=leaf,
        parent_definition=parent_definition,
        now=NOW,
    )

    assert transition.checkpoint_return is not None
    assert transition.checkpoint_return.target_checkpoint_key == "system.home"
    assert [action.type for action in transition.actions_to_execute] == [
        "send_message",
        "send_message",
    ]


def test_explicit_return_action_suppresses_the_generic_leaf_return() -> None:
    """Breaks if a never-mind leaf returns both explicitly and generically."""

    leaf = button_child(
        "system.global.never_mind",
        mode=NextFlowMode.ONE_AND_ONCE_ONLY,
        actions=[parse_action({"type": "return_to_nearest_checkpoint"})],
    )
    parent_definition = flow(
        "system.global",
        mode=NextFlowMode.CHECKPOINT,
        children=[leaf],
        return_actions=[parse_action({"type": "send_message", "text": "System"})],
    )
    parent = state("system.global", checkpoints=["system.global"])

    transition = SelectionTransitionEngine(executed_flow_keys=set()).select_child(
        parent=parent,
        child=leaf,
        parent_definition=parent_definition,
        now=NOW,
    )

    assert transition.generic_leaf_return_suppressed is True
    assert transition.checkpoint_return is None
    assert [action.type for action in transition.actions_to_execute] == [
        "return_to_nearest_checkpoint"
    ]


def test_system_checkpoint_repeats_its_return_actions() -> None:
    """Breaks if returning from the system checkpoint loses its repeat behavior."""

    system = flow(
        "system.global",
        mode=NextFlowMode.CHECKPOINT,
        return_actions=[parse_action({"type": "send_message", "text": "System"})],
    )
    branch = state(
        "system.global",
        ancestors=["system.global"],
        checkpoints=["system.global"],
    )

    transition = SelectionTransitionEngine(
        executed_flow_keys=set(), flow_definitions={system.key: system}
    ).return_to_nearest_checkpoint(branch=branch, now=NOW)

    assert transition.target_checkpoint_key == "system.global"
    assert transition.current_selection_ids == frozenset({"system.global"})
    assert transition.reusable_past_selection_ids == frozenset()
    assert [action.type for action in transition.return_actions] == ["send_message"]


@pytest.mark.parametrize("use_wrong_parent", [True, False])
def test_select_child_rejects_a_non_direct_parent_child_pair_before_deduplication(
    use_wrong_parent: bool,
) -> None:
    """Breaks if a stale parent or copied child can consume an update dedup key."""

    configured_child = button_child(
        "system.menu.help",
        mode=NextFlowMode.ALLOW_MANY,
        children=[button_child("system.menu.help.next", mode=NextFlowMode.ALLOW_MANY)],
    )
    submitted_child = (
        configured_child
        if use_wrong_parent
        else button_child(
            "system.menu.help",
            mode=NextFlowMode.ALLOW_MANY,
            children=[
                button_child("system.menu.help.next", mode=NextFlowMode.ALLOW_MANY)
            ],
        )
    )
    parent_definition = flow(
        "system.other" if use_wrong_parent else "system.menu",
        mode=NextFlowMode.ALLOW_MANY,
        children=[configured_child],
    )
    parent = state("system.menu")
    update_execution_keys: set[str] = set()

    with pytest.raises(ValueError, match="parent|direct child"):
        SelectionTransitionEngine(
            executed_flow_keys=update_execution_keys
        ).select_child(
            parent=parent,
            child=submitted_child,
            parent_definition=parent_definition,
            now=NOW,
        )

    assert update_execution_keys == set()


def test_select_child_rejects_a_parent_whose_lineage_tail_is_forged() -> None:
    """Breaks if a transition can continue from a state whose branch tail is false."""

    child = button_child(
        "system.menu.help",
        mode=NextFlowMode.ALLOW_MANY,
        children=[button_child("system.menu.help.next", mode=NextFlowMode.ALLOW_MANY)],
    )
    parent_definition = flow(
        "system.menu", mode=NextFlowMode.ALLOW_MANY, children=[child]
    )
    forged_parent = state("system.menu", ancestors=["system.other"])

    with pytest.raises(ValueError, match="ancestry tail"):
        SelectionTransitionEngine(executed_flow_keys=set()).select_child(
            parent=forged_parent,
            child=child,
            parent_definition=parent_definition,
            now=NOW,
        )


def test_return_rejects_a_foreign_checkpoint_in_forged_lineage() -> None:
    """Breaks if a branch can return to a checkpoint from another service branch."""

    system = flow("system.global", mode=NextFlowMode.CHECKPOINT)
    service_home = flow("service.zone_x.home", mode=NextFlowMode.CHECKPOINT)
    foreign_checkpoint = flow("service.other.home", mode=NextFlowMode.CHECKPOINT)
    forged_branch = state(
        "service.zone_x.capture_interest",
        service_id=uuid4(),
        ancestors=[
            "system.global",
            "service.zone_x.home",
            "service.zone_x.capture_interest",
        ],
        checkpoints=["system.global", "service.other.home"],
    )

    with pytest.raises(ValueError, match="not an ancestor"):
        SelectionTransitionEngine(
            executed_flow_keys=set(),
            flow_definitions={
                system.key: system,
                service_home.key: service_home,
                foreign_checkpoint.key: foreign_checkpoint,
            },
        ).return_to_nearest_checkpoint(branch=forged_branch, now=NOW)


def test_nested_checkpoint_return_requires_resolved_target_definition() -> None:
    """Breaks if a leaf return silently drops actions for an unresolved checkpoint."""

    system = flow("system.global", mode=NextFlowMode.CHECKPOINT)
    service_home = flow("service.zone_x.home", mode=NextFlowMode.CHECKPOINT)
    nested_branch = state(
        "service.zone_x.capture_interest",
        service_id=uuid4(),
        ancestors=[
            "system.global",
            "service.zone_x.home",
            "service.zone_x.questions",
            "service.zone_x.capture_interest",
        ],
        checkpoints=[
            "system.global",
            "service.zone_x.home",
            "service.zone_x.questions",
        ],
    )

    with pytest.raises(ValueError, match="unresolved checkpoint"):
        SelectionTransitionEngine(
            executed_flow_keys=set(),
            flow_definitions={system.key: system, service_home.key: service_home},
        ).return_to_nearest_checkpoint(branch=nested_branch, now=NOW)


def test_update_execution_scope_is_required_and_does_not_cross_updates() -> None:
    """Breaks if deduplication is implicitly retained for the engine lifetime."""

    without_scope = cast(Any, SelectionTransitionEngine)
    with pytest.raises(TypeError, match="executed_flow_keys"):
        without_scope()

    child = button_child(
        "system.menu.help",
        mode=NextFlowMode.ALLOW_MANY,
        children=[button_child("system.menu.help.next", mode=NextFlowMode.ALLOW_MANY)],
    )
    parent_definition = flow(
        "system.menu", mode=NextFlowMode.ALLOW_MANY, children=[child]
    )
    parent = state("system.menu")
    first_update_keys: set[str] = set()
    first_update_engine = SelectionTransitionEngine(
        executed_flow_keys=first_update_keys
    )
    first_update_engine.select_child(
        parent=parent,
        child=child,
        parent_definition=parent_definition,
        now=NOW,
    )

    with pytest.raises(DuplicateFlowExecutionError):
        first_update_engine.select_child(
            parent=parent,
            child=child,
            parent_definition=parent_definition,
            now=NOW,
        )

    SelectionTransitionEngine(executed_flow_keys=set()).select_child(
        parent=parent,
        child=child,
        parent_definition=parent_definition,
        now=NOW,
    )


def test_selection_lineage_is_defensively_normalized_to_immutable_tuples() -> None:
    """Breaks if callers can mutate an input or upsert lineage after creation."""

    ancestors = ["system.global", "system.menu"]
    checkpoints = ["system.global"]
    parent = state(
        "system.menu",
        ancestors=ancestors,
        checkpoints=checkpoints,
    )
    ancestors.append("forged.ancestor")
    checkpoints.append("forged.checkpoint")

    assert parent.ancestor_flow_keys == ("system.global", "system.menu")
    assert parent.checkpoint_flow_keys == ("system.global",)
    mutable_parent_lineage = cast(Any, parent.ancestor_flow_keys)
    with pytest.raises(AttributeError):
        mutable_parent_lineage.append("forged.ancestor")

    child = button_child(
        "system.menu.help",
        mode=NextFlowMode.ALLOW_MANY,
        children=[button_child("system.menu.help.next", mode=NextFlowMode.ALLOW_MANY)],
    )
    system = flow("system.global", mode=NextFlowMode.CHECKPOINT)
    transition = SelectionTransitionEngine(
        executed_flow_keys=set(),
        flow_definitions={system.key: system},
    ).select_child(
        parent=parent,
        child=child,
        parent_definition=flow(
            "system.menu", mode=NextFlowMode.ALLOW_MANY, children=[child]
        ),
        now=NOW,
    )

    mutable_upsert_lineage = cast(
        Any, transition.upsert_selections[0].ancestor_flow_keys
    )
    with pytest.raises(AttributeError):
        mutable_upsert_lineage.append("forged.ancestor")
