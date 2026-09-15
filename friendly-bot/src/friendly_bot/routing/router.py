"""Assembly and iterative selection of configured discussion-flow keys."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from friendly_bot.domain.flows import DiscussionFlow
from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.domain.state import OpenSelectionState
from friendly_bot.persistence.uow import UnitOfWork, UnitOfWorkFactory
from friendly_bot.responses.planner import (
    CandidateResponsePlan,
    PlannedActionText,
    ReplyPlan,
    message_gists,
    plan_candidate_responses,
)
from friendly_bot.routing.contracts import (
    KnownFlowRequest,
    MultiIntentMatches,
    MultiIntentRequest,
    MultiIntentTerminal,
    PlannedFlowMatch,
    RoutingPromptCandidate,
)


class RoutingError(RuntimeError):
    """The configured routing graph cannot safely yield a result."""


class DuplicateRoutingCandidateError(RoutingError):
    """Two published selections expose the same message-trigger key."""


class RoutingTerminal(StrEnum):
    """The only ways a one-shot typed-routing update may terminate."""

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
    gists: tuple[str, ...]
    source: str
    is_current: bool
    service_id: UUID | None
    is_global_interruptive: bool
    multi_intent_mode: Literal["answer", "interactive"]
    response_plan: CandidateResponsePlan
    flow_version_id: UUID | None = None

    @property
    def gist(self) -> str:
        """Keep the staged key-selection transport on the first typed gist."""

        return self.gists[0]


@dataclass(frozen=True, slots=True)
class RoutedMatch:
    """One local routing candidate paired with its validated reply plan."""

    candidate: RoutingCandidate
    reply_plan: ReplyPlan


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Temporary compatibility projection for callers not yet moved to match policy."""

    key: str


@dataclass(frozen=True, slots=True)
class MultiIntentRoutingResult:
    """Answers, one interactive match, deferred candidates, or a typed terminal."""

    answers: tuple[RoutedMatch, ...]
    interactive: RoutedMatch | None
    deferred: tuple[RoutingCandidate, ...]
    terminal: RoutingTerminal | None

    @property
    def selected_keys(self) -> tuple[RoutingDecision, ...]:
        """Project matches for the staged application migration in Task 7."""

        matches = self.answers + ((self.interactive,) if self.interactive else ())
        return tuple(RoutingDecision(match.candidate.key) for match in matches)


class ResponseModel(Protocol):
    """The one-shot model operation used by typed message routing."""

    async def route_and_plan(
        self, request: MultiIntentRequest
    ) -> MultiIntentMatches | MultiIntentTerminal: ...

    async def plan_known_flow(self, request: KnownFlowRequest) -> PlannedFlowMatch: ...


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
                gists = message_gists(child.trigger)
                if not gists:
                    continue
                key = str(child.key)
                if key in seen_keys:
                    raise DuplicateRoutingCandidateError(key)
                seen_keys.add(key)
                candidates.append(
                    RoutingCandidate(
                        key=key,
                        gists=gists,
                        source=_source_label(selection),
                        is_current=selection.is_current,
                        service_id=selection.service_id,
                        is_global_interruptive=selection.is_global_interruptive,
                        multi_intent_mode=child.multi_intent_mode.value,
                        response_plan=plan_candidate_responses(
                            child,
                            checkpoint=_checkpoint_for(selection, root, parent, child),
                        ),
                        flow_version_id=selection.flow_version_id,
                    )
                )
        return candidates


class ConstrainedRouter:
    """Build one candidate request and apply local multi-intent safety policy."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        gateway: ResponseModel,
        *,
        candidate_assembler: CandidateAssembler | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._gateway = gateway
        self._candidate_assembler = candidate_assembler or CandidateAssembler()

    async def route_update(
        self, user_id: UUID, incoming: IncomingText, now: datetime
    ) -> MultiIntentRoutingResult:
        """Route a typed update once without an iterative terminal sentinel."""

        async with self._uow_factory() as uow:
            return await self._route_update(
                uow,
                user_id=user_id,
                incoming=incoming,
                now=now,
                incoming_is_persisted=False,
            )

    async def plan_known_flow(self, request: KnownFlowRequest) -> PlannedFlowMatch:
        """Plan ordinary copy for one already-selected local flow once."""

        return await self._gateway.plan_known_flow(request)

    async def route_update_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        incoming: IncomingText,
        now: datetime,
    ) -> MultiIntentRoutingResult:
        """Route in T02's locked ingress transaction without opening a second session."""

        return await self._route_update(
            uow,
            user_id=user_id,
            incoming=incoming,
            now=now,
            incoming_is_persisted=True,
        )

    async def _route_update(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        incoming: IncomingText,
        now: datetime,
        incoming_is_persisted: bool,
    ) -> MultiIntentRoutingResult:
        """Plan all matching configured flows using a prompt containing input once."""

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
        messages = tuple(message.body for message in unsummarized)
        if not incoming_is_persisted:
            messages += (incoming.body,)
        if not candidates:
            return MultiIntentRoutingResult((), None, (), RoutingTerminal.NO_MATCH)
        model_result = await self._gateway.route_and_plan(
            MultiIntentRequest(
                persona=cursor.persona,
                messages=messages,
                reply_body=incoming.replied_to_body,
                candidates=tuple(
                    RoutingPromptCandidate(
                        flow_id=candidate.key,
                        gists=candidate.gists,
                        context_label=candidate.source,
                        multi_intent_mode=candidate.multi_intent_mode,
                        reply_slots=candidate.response_plan.reply_slots,
                    )
                    for candidate in candidates
                ),
            )
        )
        if isinstance(model_result, MultiIntentTerminal):
            return MultiIntentRoutingResult(
                (), None, (), RoutingTerminal(model_result.terminal)
            )
        matches = _routed_matches(model_result, candidates)
        from friendly_bot.routing.policy import MultiIntentPolicy

        return MultiIntentPolicy().partition(matches)


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


def _checkpoint_for(
    selection: OpenSelectionState,
    root: DiscussionFlow,
    parent: DiscussionFlow,
    child: DiscussionFlow,
) -> DiscussionFlow | None:
    """Find the checkpoint whose return actions can follow this immediate closure."""

    if child.next_flow_mode.value == "checkpoint":
        return child
    if parent.next_flow_mode.value == "checkpoint":
        return parent
    if not selection.checkpoint_flow_keys:
        return None
    checkpoint = _find_flow(root, selection.checkpoint_flow_keys[-1])
    if checkpoint is None:
        raise RoutingError("open selection checkpoint is absent from its definition")
    return checkpoint


def _routed_matches(
    model_result: MultiIntentMatches, candidates: list[RoutingCandidate]
) -> tuple[RoutedMatch, ...]:
    """Pair provider-validated match IDs with local candidate and slot bindings."""

    by_flow_id = {candidate.key: candidate for candidate in candidates}
    if len(by_flow_id) != len(candidates):
        raise DuplicateRoutingCandidateError("duplicate candidate key")
    matches: list[RoutedMatch] = []
    seen_flow_ids: set[str] = set()
    for model_match in model_result.matches:
        if model_match.flow_id in seen_flow_ids:
            raise RoutingError("model returned a duplicate flow key")
        seen_flow_ids.add(model_match.flow_id)
        candidate = by_flow_id.get(model_match.flow_id)
        if candidate is None:
            raise RoutingError("model returned an unknown flow key")
        matches.append(RoutedMatch(candidate, _reply_plan_for(model_match, candidate)))
    return tuple(matches)


def _reply_plan_for(
    model_match: PlannedFlowMatch, candidate: RoutingCandidate
) -> ReplyPlan:
    """Bind locally validated slot responses back to their action addresses."""

    replies = {reply.slot_id: reply.text for reply in model_match.replies}
    bindings = candidate.response_plan.bindings
    if len(replies) != len(model_match.replies) or set(replies) != {
        binding.slot_id for binding in bindings
    }:
        raise RoutingError("model reply slots do not match the local candidate")
    return ReplyPlan(
        tuple(
            PlannedActionText(
                binding.flow_key,
                binding.action_index,
                replies[binding.slot_id],
            )
            for binding in bindings
        )
    )
