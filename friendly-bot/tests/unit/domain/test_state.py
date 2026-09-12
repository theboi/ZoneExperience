"""Tests for pure discussion-flow selection transitions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from friendly_bot.domain.actions import parse_action
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
    actions: list[object] | None = None,
    return_actions: list[object] | None = None,
) -> DiscussionFlow:
    """Build a small flow definition without invoking any runtime executor."""

    return DiscussionFlow(
        key=key,
        actions=actions or [],  # type: ignore[arg-type]
        next_flows=children or [],
        next_flow_mode=mode,
        return_actions=return_actions or [],  # type: ignore[arg-type]
    )


def button_child(
    key: str,
    *,
    mode: NextFlowMode,
    children: list[DiscussionFlow] | None = None,
    actions: list[object] | None = None,
) -> DiscussionFlow:
    """Build a selectable child with a stable, transport-neutral button trigger."""

    return DiscussionFlow(
        key=key,
        trigger=parse_trigger({"type": "button", "button_id": key}),
        actions=actions or [],  # type: ignore[arg-type]
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
        ancestor_flow_keys=ancestors or [key],
        checkpoint_flow_keys=checkpoints or [],
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

    transition = SelectionTransitionEngine().select_child(
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

    transition = SelectionTransitionEngine().select_child(
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

    transition = SelectionTransitionEngine().select_child(
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

    transition = SelectionTransitionEngine().select_child(
        parent=parent,
        child=child,
        parent_definition=parent_definition,
        now=NOW,
    )

    child_selection = transition.upsert_selections[0]
    assert child_selection.ancestor_flow_keys == [
        "system.global",
        "service.zone_x.home",
        "service.zone_x.questions",
    ]
    assert child_selection.checkpoint_flow_keys == [
        "system.global",
        "service.zone_x.home",
        "service.zone_x.questions",
    ]


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
        flow_definitions={
            system.key: system,
            service_home.key: service_home,
            questions.key: questions,
        }
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

    transition = SelectionTransitionEngine().select_child(
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

    transition = SelectionTransitionEngine().select_child(
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
        flow_definitions={system.key: system}
    ).return_to_nearest_checkpoint(branch=branch, now=NOW)

    assert transition.target_checkpoint_key == "system.global"
    assert transition.current_selection_ids == frozenset({"system.global"})
    assert transition.reusable_past_selection_ids == frozenset()
    assert [action.type for action in transition.return_actions] == ["send_message"]
