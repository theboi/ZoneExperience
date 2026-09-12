"""Pure, data-only transitions for open discussion-flow selections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from friendly_bot.domain.actions import DiscussionAction
from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode


class DuplicateFlowExecutionError(RuntimeError):
    """Raised when an incoming update attempts to run one child twice."""

    def __init__(self, flow_key: str) -> None:
        super().__init__(f"flow {flow_key!r} already executed for this update")
        self.flow_key = flow_key


class MissingCheckpointError(RuntimeError):
    """Raised when a branch cannot be returned to any checkpoint."""

    def __init__(self, flow_key: str) -> None:
        super().__init__(f"flow {flow_key!r} has no checkpoint to return to")
        self.flow_key = flow_key


@dataclass(frozen=True, slots=True)
class OpenSelectionState:
    """The durable state needed to describe one open branch selection."""

    id: UUID
    user_id: UUID
    flow_version_id: UUID
    parent_flow_key: str
    service_id: UUID | None
    is_current: bool
    is_global_interruptive: bool
    ancestor_flow_keys: list[str]
    checkpoint_flow_keys: list[str]
    opened_at: datetime
    last_focused_at: datetime


@dataclass(frozen=True, slots=True)
class CheckpointReturnTransition:
    """A branch-local request to focus a checkpoint and run its return actions."""

    target_checkpoint_key: str
    current_selection_ids: frozenset[str]
    reusable_past_selection_ids: frozenset[UUID]
    return_actions: tuple[DiscussionAction, ...]
    focused_at: datetime


@dataclass(frozen=True, slots=True)
class SelectionTransition:
    """The state mutation and declarative action effects of selecting one child."""

    delete_selection_ids: frozenset[UUID]
    reusable_past_selection_ids: frozenset[UUID]
    current_selection_ids: frozenset[str]
    upsert_selections: tuple[OpenSelectionState, ...]
    child_actions: tuple[DiscussionAction, ...]
    checkpoint_return: CheckpointReturnTransition | None
    generic_leaf_return_suppressed: bool

    @property
    def actions_to_execute(self) -> tuple[DiscussionAction, ...]:
        """Return ordered declarative effects without executing any action."""

        if self.checkpoint_return is None:
            return self.child_actions
        return self.child_actions + self.checkpoint_return.return_actions

    @property
    def selection_upserts(self) -> tuple[OpenSelectionState, ...]:
        """Expose the upsert rows under the persistence-oriented noun order."""

        return self.upsert_selections


class SelectionTransitionEngine:
    """Describe one incoming update's state changes without external side effects."""

    def __init__(
        self,
        executed_flow_keys: set[str] | None = None,
        *,
        flow_definitions: Mapping[str, DiscussionFlow] | None = None,
    ) -> None:
        self._executed_flow_keys = (
            executed_flow_keys if executed_flow_keys is not None else set()
        )
        self._flow_definitions = dict(flow_definitions or {})

    def select_child(
        self,
        *,
        parent: OpenSelectionState,
        child: DiscussionFlow,
        parent_definition: DiscussionFlow,
        now: datetime,
    ) -> SelectionTransition:
        """Describe selecting ``child`` from ``parent`` for one incoming update."""

        if child.key in self._executed_flow_keys:
            raise DuplicateFlowExecutionError(child.key)
        self._executed_flow_keys.add(child.key)

        delete_selection_ids, reusable_past_selection_ids = self._parent_mutation(
            parent=parent,
            parent_definition=parent_definition,
        )
        checkpoint_flow_keys = self._child_checkpoint_lineage(
            parent=parent,
            parent_definition=parent_definition,
            child=child,
        )
        child_actions = tuple(child.actions)

        if child.next_flows:
            child_selection = OpenSelectionState(
                id=uuid5(parent.id, str(child.key)),
                user_id=parent.user_id,
                flow_version_id=parent.flow_version_id,
                parent_flow_key=str(child.key),
                service_id=parent.service_id,
                is_current=True,
                is_global_interruptive=parent.is_global_interruptive,
                ancestor_flow_keys=[*parent.ancestor_flow_keys, str(child.key)],
                checkpoint_flow_keys=checkpoint_flow_keys,
                opened_at=now,
                last_focused_at=now,
            )
            return SelectionTransition(
                delete_selection_ids=delete_selection_ids,
                reusable_past_selection_ids=reusable_past_selection_ids,
                current_selection_ids=frozenset({str(child.key)}),
                upsert_selections=(child_selection,),
                child_actions=child_actions,
                checkpoint_return=None,
                generic_leaf_return_suppressed=False,
            )

        generic_leaf_return_suppressed = _has_explicit_return(child_actions)
        checkpoint_return = None
        if not generic_leaf_return_suppressed:
            checkpoint_return = self._generic_leaf_return(
                parent=parent,
                parent_definition=parent_definition,
                checkpoint_flow_keys=checkpoint_flow_keys,
                now=now,
            )

        return SelectionTransition(
            delete_selection_ids=delete_selection_ids,
            reusable_past_selection_ids=reusable_past_selection_ids,
            current_selection_ids=(
                checkpoint_return.current_selection_ids
                if checkpoint_return is not None
                else frozenset()
            ),
            upsert_selections=(),
            child_actions=child_actions,
            checkpoint_return=checkpoint_return,
            generic_leaf_return_suppressed=generic_leaf_return_suppressed,
        )

    def return_to_nearest_checkpoint(
        self,
        *,
        branch: OpenSelectionState,
        now: datetime,
    ) -> CheckpointReturnTransition:
        """Describe an explicit branch-local return, popping its current checkpoint."""

        checkpoint_flow_keys = tuple(branch.checkpoint_flow_keys)
        if not checkpoint_flow_keys:
            raise MissingCheckpointError(branch.parent_flow_key)

        current_checkpoint_key = checkpoint_flow_keys[-1]
        branch_is_current_checkpoint = branch.parent_flow_key == current_checkpoint_key
        if branch_is_current_checkpoint:
            target_checkpoint_key = self._popped_checkpoint_target(
                branch=branch,
                checkpoint_flow_keys=checkpoint_flow_keys,
            )
        else:
            target_checkpoint_key = current_checkpoint_key

        reusable_past_selection_ids = frozenset[UUID]()
        if (
            branch_is_current_checkpoint
            and target_checkpoint_key != branch.parent_flow_key
        ):
            reusable_past_selection_ids = frozenset({branch.id})

        return CheckpointReturnTransition(
            target_checkpoint_key=target_checkpoint_key,
            current_selection_ids=frozenset({target_checkpoint_key}),
            reusable_past_selection_ids=reusable_past_selection_ids,
            return_actions=self._return_actions_for(target_checkpoint_key),
            focused_at=now,
        )

    def _parent_mutation(
        self,
        *,
        parent: OpenSelectionState,
        parent_definition: DiscussionFlow,
    ) -> tuple[frozenset[UUID], frozenset[UUID]]:
        if parent_definition.next_flow_mode is NextFlowMode.ONE_AND_ONCE_ONLY:
            return frozenset({parent.id}), frozenset()
        if parent.is_current:
            return frozenset(), frozenset({parent.id})
        return frozenset(), frozenset()

    def _child_checkpoint_lineage(
        self,
        *,
        parent: OpenSelectionState,
        parent_definition: DiscussionFlow,
        child: DiscussionFlow,
    ) -> list[str]:
        checkpoint_flow_keys = list(parent.checkpoint_flow_keys)
        if parent_definition.next_flow_mode is NextFlowMode.CHECKPOINT:
            _append_once(checkpoint_flow_keys, str(parent_definition.key))
        if child.next_flow_mode is NextFlowMode.CHECKPOINT:
            _append_once(checkpoint_flow_keys, str(child.key))
        return checkpoint_flow_keys

    def _generic_leaf_return(
        self,
        *,
        parent: OpenSelectionState,
        parent_definition: DiscussionFlow,
        checkpoint_flow_keys: list[str],
        now: datetime,
    ) -> CheckpointReturnTransition:
        if not checkpoint_flow_keys:
            raise MissingCheckpointError(parent.parent_flow_key)
        target_checkpoint_key = checkpoint_flow_keys[-1]
        return_actions = (
            tuple(parent_definition.return_actions)
            if target_checkpoint_key == parent_definition.key
            else self._return_actions_for(target_checkpoint_key)
        )
        return CheckpointReturnTransition(
            target_checkpoint_key=target_checkpoint_key,
            current_selection_ids=frozenset({target_checkpoint_key}),
            reusable_past_selection_ids=frozenset(),
            return_actions=return_actions,
            focused_at=now,
        )

    def _popped_checkpoint_target(
        self,
        *,
        branch: OpenSelectionState,
        checkpoint_flow_keys: tuple[str, ...],
    ) -> str:
        if len(checkpoint_flow_keys) > 1:
            return checkpoint_flow_keys[-2]
        if branch.service_id is None:
            return checkpoint_flow_keys[-1]
        raise MissingCheckpointError(branch.parent_flow_key)

    def _return_actions_for(
        self, target_checkpoint_key: str
    ) -> tuple[DiscussionAction, ...]:
        definition = self._flow_definitions.get(target_checkpoint_key)
        if definition is None:
            return ()
        return tuple(definition.return_actions)


def _append_once(keys: list[str], key: str) -> None:
    if key not in keys:
        keys.append(key)


def _has_explicit_return(actions: tuple[DiscussionAction, ...]) -> bool:
    return any(action.type == "return_to_nearest_checkpoint" for action in actions)
