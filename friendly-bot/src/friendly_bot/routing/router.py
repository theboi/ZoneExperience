"""Assembly and iterative selection of configured discussion-flow keys."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from friendly_bot.domain.flows import DiscussionFlow
from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.domain.state import OpenSelectionState
from friendly_bot.domain.triggers import MessageDiscussionFlowTrigger
from friendly_bot.hyperparameters import ROUTING_MAX_ATTEMPTS
from friendly_bot.persistence.uow import UnitOfWork, UnitOfWorkFactory
from friendly_bot.routing.contracts import KeySelectionRequest, RoutingPromptCandidate

_RESERVED_TERMINALS = frozenset(
    {
        "system.done",
        "system.no_match",
        "system.clarify_ambiguous_context",
    }
)


class RoutingError(RuntimeError):
    """The configured routing graph cannot safely yield a result."""


class DuplicateRoutingCandidateError(RoutingError):
    """Two published selections expose the same message-trigger key."""


class RoutingTerminal(StrEnum):
    """The only ways a key-only routing update may terminate."""

    DONE = "done"
    NO_MATCH = "no_match"
    CLARIFY = "clarify_ambiguous_context"


@dataclass(frozen=True, slots=True)
class IncomingText:
    """User-authored text used as routing context, without source-message identity."""

    body: str
    replied_to_body: str | None = None


@dataclass(frozen=True, slots=True)
class RoutingCandidate:
    """One configured message choice available for a single update."""

    key: str
    gist: str
    source: str
    is_current: bool
    service_id: UUID | None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """A configured key selected during the current update."""

    key: str


@dataclass(frozen=True, slots=True)
class RoutingResult:
    """Ordered flow-key decisions followed by exactly one terminal state."""

    selected_keys: tuple[RoutingDecision, ...]
    terminal: RoutingTerminal


class KeySelector(Protocol):
    """The narrow model operation R03 needs for constrained routing."""

    async def select_key(self, request: KeySelectionRequest) -> str: ...


class CandidateAssembler:
    """Reconstruct direct message choices from immutable published definitions."""

    def assemble(
        self,
        selections: Sequence[OpenSelectionState],
        definitions: Mapping[UUID, PublishedFlowDefinition],
        *,
        now: datetime,
    ) -> list[RoutingCandidate]:
        """Return message-trigger children for all valid supplied selection scopes."""

        del now
        candidates: list[RoutingCandidate] = []
        seen_keys: set[str] = set()
        for selection in selections:
            definition = definitions.get(selection.flow_version_id)
            if definition is None:
                raise RoutingError("open selection has no immutable flow definition")
            root = DiscussionFlow.model_validate(definition.document)
            parent = _find_flow(root, selection.parent_flow_key)
            if parent is None:
                raise RoutingError(
                    "open selection parent is absent from its definition"
                )
            for child in parent.next_flows:
                trigger = child.trigger
                if not isinstance(trigger, MessageDiscussionFlowTrigger):
                    continue
                key = str(child.key)
                if key in seen_keys:
                    raise DuplicateRoutingCandidateError(key)
                seen_keys.add(key)
                candidates.append(
                    RoutingCandidate(
                        key=key,
                        gist=trigger.llm_gist,
                        source=_source_label(selection),
                        is_current=selection.is_current,
                        service_id=selection.service_id,
                    )
                )
        return candidates


class ConstrainedRouter:
    """Ask only for configured keys, removing each accepted key before re-asking."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        gateway: KeySelector,
        *,
        candidate_assembler: CandidateAssembler | None = None,
        max_attempts: int = ROUTING_MAX_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self._uow_factory = uow_factory
        self._gateway = gateway
        self._candidate_assembler = candidate_assembler or CandidateAssembler()
        self._max_attempts = max_attempts

    async def route_update(
        self, user_id: UUID, incoming: IncomingText, now: datetime
    ) -> RoutingResult:
        """Select configured keys until the gateway returns one reserved terminal."""

        async with self._uow_factory() as uow:
            return await self.route_update_in_uow(
                uow, user_id=user_id, incoming=incoming, now=now
            )

    async def route_update_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        incoming: IncomingText,
        now: datetime,
    ) -> RoutingResult:
        """Route in T02's locked ingress transaction without opening a second session."""

        selections = await uow.open_selections.list_for_user(user_id, now=now)
        valid_selections = await _valid_selections(uow, selections, now)
        cursor = await uow.personas.get_or_create(user_id)
        unsummarized = await uow.conversations.list_after(
            user_id, cursor.last_message_id
        )
        definitions = await _load_definitions(uow, valid_selections)

        candidates = self._candidate_assembler.assemble(
            valid_selections, definitions, now=now
        )
        remaining = {candidate.key: candidate for candidate in candidates}
        selected: list[RoutingDecision] = []
        messages = tuple(message.body for message in unsummarized) + (incoming.body,)
        for _ in range(self._max_attempts):
            request = KeySelectionRequest(
                allowed_keys=frozenset(remaining) | _RESERVED_TERMINALS,
                persona=cursor.persona,
                messages=messages,
                reply_body=incoming.replied_to_body,
                candidates=tuple(
                    RoutingPromptCandidate(
                        key=candidate.key,
                        gist=candidate.gist,
                        context_label=candidate.source,
                    )
                    for candidate in remaining.values()
                ),
            )
            key = await self._gateway.select_key(request)
            terminal = _terminal_for(key)
            if terminal is not None:
                return RoutingResult(tuple(selected), terminal)
            candidate = remaining.pop(key, None)
            if candidate is None:
                raise RoutingError(
                    "gateway selected a key outside the shrinking candidate set"
                )
            selected.append(RoutingDecision(candidate.key))
        raise RoutingError("gateway did not return a reserved terminal in time")


async def _valid_selections(
    uow: UnitOfWork, selections: Sequence[OpenSelectionState], now: datetime
) -> list[OpenSelectionState]:
    valid: list[OpenSelectionState] = []
    for selection in selections:
        if selection.service_id is None:
            valid.append(selection)
            continue
        service = await uow.services.get(selection.service_id)
        if service.interaction_ends_at > now:
            valid.append(selection)
    return valid


async def _load_definitions(
    uow: UnitOfWork, selections: Sequence[OpenSelectionState]
) -> dict[UUID, PublishedFlowDefinition]:
    definitions: dict[UUID, PublishedFlowDefinition] = {}
    for selection in selections:
        if selection.flow_version_id in definitions:
            continue
        version = await uow.flow_versions.get(selection.flow_version_id)
        definitions[selection.flow_version_id] = PublishedFlowDefinition(
            document=version.definition,
            flow_key_index={},
        )
    return definitions


def _find_flow(root: DiscussionFlow, key: str) -> DiscussionFlow | None:
    stack = [root]
    while stack:
        flow = stack.pop()
        if flow.key == key:
            return flow
        stack.extend(reversed(flow.next_flows))
    return None


def _source_label(selection: OpenSelectionState) -> str:
    if selection.is_current:
        return "current"
    if selection.service_id is not None:
        return "service"
    if selection.is_global_interruptive:
        return "system"
    return "reusable"


def _terminal_for(key: str) -> RoutingTerminal | None:
    if key == "system.done":
        return RoutingTerminal.DONE
    if key == "system.no_match":
        return RoutingTerminal.NO_MATCH
    if key == "system.clarify_ambiguous_context":
        return RoutingTerminal.CLARIFY
    return None
