"""I04 seed publication and runtime composition primitives."""

from __future__ import annotations

import asyncio
import logging
import runpy
import signal
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from friendly_bot.actions.context import ActionContext, ActionNavigation
from friendly_bot.actions.registry import (
    ActionDependencies,
    ActionExecutorRegistry,
    build_action_registry,
)
from friendly_bot.actions.runner import DEFAULT_UNHANDLED_ERROR_TEXT, ActionRunner
from friendly_bot.config.settings import (
    PROJECT_ROOT,
    DatabaseSettings,
    TelegramSettings,
)
from friendly_bot.domain.actions import SelectServiceAttendanceAction
from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode
from friendly_bot.domain.publication import (
    PublishedFlowDefinition,
    RootKind,
    TemplateContextSchema,
    validate_for_publication,
)
from friendly_bot.domain.state import OpenSelectionState, SelectionTransitionEngine
from friendly_bot.domain.triggers import (
    DiscussionTrigger,
    OnActionEventTrigger,
    OnAnyMessageTrigger,
    OnAnyOfTrigger,
    OnButtonPressTrigger,
    OnCommandTrigger,
    OnMessageTrigger,
)
from friendly_bot.error_logs import error_log_name, write_error_log
from friendly_bot.intents import PendingIntentService
from friendly_bot.matching.service import MatchingService
from friendly_bot.onboarding.accounts import (
    OperationalAccountService,
    normalize_operational_name,
)
from friendly_bot.onboarding.service import OnboardingService
from friendly_bot.persistence.connection import DirectPostgresConnectionFactory
from friendly_bot.persistence.models import (
    FlowScopeKind,
    OperationalRole,
    ServiceAudience,
)
from friendly_bot.persistence.repositories import (
    FlowVersionRecord,
    MatchRequestRecord,
    NewService,
    NewServiceTimestamp,
    PendingFlowIntentRecord,
    ServiceRecord,
    ServiceTimestampRecord,
    UserRecord,
)
from friendly_bot.persistence.uow import UnitOfWork, UnitOfWorkFactory
from friendly_bot.responses.planner import (
    CandidateResponsePlan,
    PlannedActionText,
    ReplyPlan,
    plan_candidate_responses,
)
from friendly_bot.routing.contracts import KnownFlowRequest, PlannedFlowMatch
from friendly_bot.routing.openrouter_gateway import (
    GatewayProtocolError,
    GatewayTransportError,
    OpenRouterGateway,
)
from friendly_bot.routing.router import (
    ConstrainedRouter,
    IncomingText,
    RoutedMatch,
    RoutingTerminal,
)
from friendly_bot.services import (
    AudienceResolver,
    ServiceAttendanceService,
    ServiceDeliveryScheduler,
    ServiceLifecycleService,
)
from friendly_bot.services.scheduler import TimestampRootPreparation
from friendly_bot.telegram import (
    BestEffortTelegramSender,
    LocalTelegramAssetResolver,
    PresentationBuffer,
    TelegramApiClient,
    TelegramApiError,
    TelegramCallback,
    TelegramCallbackContextKind,
    TelegramIngress,
    TelegramMessage,
    TelegramPoller,
    TelegramPreflight,
    TelegramPresentation,
    TelegramResponseUncertain,
    TelegramRuntimeLock,
    TelegramTextPresentation,
    normalize_command,
)

LOGGER = logging.getLogger(__name__)

type DispatchKind = Literal[
    "selected", "no_match", "clarified", "ignored", "expired", "failed", "onboarding"
]

_ZONE_X_TEMPLATE_CONTEXT: Final = TemplateContextSchema(
    {
        "active_service.id",
        "human_match_request.interest",
        "matched_human.name",
        "matched_human.telegram_url",
        "matched_server.cg_name",
        "matched_server.name",
        "matched_server.telegram_url",
        "service.id",
        "service.map_url",
        "service.name",
        "user.display_name",
        "user.name",
    }
)


class SeedModuleLoadError(RuntimeError):
    """Raised when a local Python seed module cannot be loaded safely."""


class ZoneXTimestampSeed(BaseModel):
    """One canonical timestamp root decoded from the Python source of truth."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    occurs_at: datetime
    audience: ServiceAudience
    root_flow: DiscussionFlow


class ZoneXServiceSeed(BaseModel):
    """One service and every root I04 must publish without ORM access."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    name: str
    map_url: str
    timezone: str
    highkey: bool
    doors_open_at: datetime
    doors_close_at: datetime
    service_starts_at: datetime
    service_ends_at: datetime
    interaction_ends_at: datetime
    service_global_root: DiscussionFlow
    latecomer_flow: DiscussionFlow
    timestamps: tuple[ZoneXTimestampSeed, ...] = Field(min_length=1)


class ZoneXSeed(BaseModel):
    """One service-specific Zone X Python seed module."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service: ZoneXServiceSeed


class OperationalProfileSeed(BaseModel):
    """One pre-authorized server, leader, or staff profile from local seed data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=256)
    dob: date
    role: OperationalRole
    interests: tuple[str, ...] = ()
    cg_name: str | None = None
    telegram_contact_url: str | None = None
    always_available: bool = False
    capacity: int = Field(ge=0)
    is_admin: bool = False


class SystemGlobalSeed(BaseModel):
    """The system-wide root that is shared by every service seed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    root: DiscussionFlow
    operational_profiles: tuple[OperationalProfileSeed, ...] = ()


@dataclass(frozen=True, slots=True)
class PublishedZoneX:
    """The immutable flow records and live service identity needed for runtime entry."""

    service: ServiceRecord
    map_url: str
    system_root: FlowVersionRecord
    service_root: FlowVersionRecord
    latecomer_root: FlowVersionRecord
    timestamps: tuple[ServiceTimestampRecord, ...]

    def timestamp_for_key(self, key: str) -> ServiceTimestampRecord:
        """Return one canonical timestamp by stable source key."""

        for timestamp in self.timestamps:
            if timestamp.key == key:
                return timestamp
        raise LookupError("Zone X timestamp was not found")


@dataclass(frozen=True, slots=True)
class RuntimeRoutingPolicy:
    """The only local copy permitted for R03 routing terminal responses."""

    no_match_text: str | None = None

    def __post_init__(self) -> None:
        if self.no_match_text is not None and (
            type(self.no_match_text) is not str or not self.no_match_text
        ):
            raise ValueError("no-match text must be nonempty when configured")


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """One ingress decision and its post-commit Telegram presentations."""

    kind: DispatchKind
    executed_flow_keys: tuple[str, ...] = ()
    presentations: tuple[TelegramPresentation, ...] = ()

    @property
    def selected_flow_keys(self) -> tuple[str, ...]:
        """Expose the staged name while callers move to execution terminology."""

        return self.executed_flow_keys


@dataclass(frozen=True, slots=True)
class _DirectSelection:
    """One exact local trigger selection after callback context revalidation."""

    branch: OpenSelectionState
    version: FlowVersionRecord
    root: DiscussionFlow
    parent: DiscussionFlow
    child: DiscussionFlow
    service: ServiceRecord | None
    match_request: MatchRequestRecord | None


@dataclass(frozen=True, slots=True)
class _ExecutedInteractive:
    """The completed transition and action path for one interactive choice."""

    executed_flow_keys: tuple[str, ...]
    completed: bool


class FriendlyBotApplication:
    """Compose F01, T02, and R03 inside the caller-owned Telegram ingress UoW."""

    def __init__(
        self,
        *,
        dependencies: ActionDependencies,
        registry: ActionExecutorRegistry,
        router: ConstrainedRouter,
        zone_x: PublishedZoneX,
        routing_policy: RuntimeRoutingPolicy | None = None,
        onboarding: OnboardingService | None = None,
    ) -> None:
        self._dependencies = dependencies
        self._runner = ActionRunner(registry)
        self._router = router
        self._zone_x = zone_x
        self._routing_policy = routing_policy or RuntimeRoutingPolicy()
        self._onboarding = onboarding
        self._navigation = _ApplicationNavigation(self)

    async def dispatch(
        self,
        *,
        user_id: UUID,
        incoming: TelegramMessage,
        unit_of_work: UnitOfWork,
    ) -> DispatchResult:
        """Dispatch one already-claimed, already-persisted Telegram input once."""

        user = await unit_of_work.users.require_by_id(user_id)
        await unit_of_work.lock_user(user.id)
        now = incoming.sent_at
        correlation_id = uuid4()
        presentations = PresentationBuffer()

        if incoming.callback is not None:
            result = await self._dispatch_callback(
                user=user,
                incoming=incoming,
                unit_of_work=unit_of_work,
                correlation_id=correlation_id,
                now=now,
                presentations=presentations,
            )
            return self._with_presentations(result, presentations)

        assert incoming.text is not None
        onboarding = await self._dispatch_onboarding(
            user=user,
            incoming=incoming,
            unit_of_work=unit_of_work,
            correlation_id=correlation_id,
            now=now,
            presentations=presentations,
        )
        if onboarding is not None:
            return self._with_presentations(onboarding, presentations)
        branches = await self._valid_branches(unit_of_work, user.id, now=now)
        if not branches:
            await self.open_system_root_for_user(
                user_id=user.id,
                unit_of_work=unit_of_work,
                now=now,
                presentations=presentations,
            )
        elif _requires_system_root_reset(
            branches, current_version_id=self._zone_x.system_root.id
        ):
            await unit_of_work.open_selections.reset_global_root(
                _root_selection(
                    user=user,
                    version=self._zone_x.system_root,
                    root=_root_from_version(self._zone_x.system_root),
                    now=now,
                ),
                at=now,
            )
        command = normalize_command(incoming.text)
        if command.startswith("/"):
            selection = await self._find_direct_selection(
                unit_of_work=unit_of_work,
                user=user,
                now=now,
                matcher=lambda child: _matches_command(child.trigger, command),
            )
            if selection is not None:
                executed = await self._execute_selection(
                    selection,
                    user=user,
                    incoming=incoming,
                    unit_of_work=unit_of_work,
                    correlation_id=correlation_id,
                    now=now,
                    executed_flow_keys=set(),
                    presentations=presentations,
                )
                return self._result(
                    "selected", executed.executed_flow_keys, presentations
                )

        deterministic_capture = await self._find_direct_selection(
            unit_of_work=unit_of_work,
            user=user,
            now=now,
            matcher=lambda child: _matches_any_message(child.trigger),
            skip_invalid_branches=True,
        )
        if deterministic_capture is not None:
            executed = await self._execute_selection(
                deterministic_capture,
                user=user,
                incoming=incoming,
                unit_of_work=unit_of_work,
                correlation_id=correlation_id,
                now=now,
                executed_flow_keys=set(),
                presentations=presentations,
            )
            return self._result("selected", executed.executed_flow_keys, presentations)

        try:
            routing = await self._router.route_update_in_uow(
                unit_of_work,
                user_id=user.id,
                incoming=IncomingText(
                    body=incoming.text, replied_to_body=incoming.reply_text
                ),
                now=now,
            )
        except GatewayTransportError as error:
            return await self._routing_failure_result(
                unit_of_work,
                user=user,
                correlation_id=correlation_id,
                now=now,
                reason_code="routing.provider_unavailable",
                error=error,
                presentations=presentations,
            )
        except GatewayProtocolError as error:
            return await self._routing_failure_result(
                unit_of_work,
                user=user,
                correlation_id=correlation_id,
                now=now,
                reason_code="routing.provider_invalid_response",
                error=error,
                presentations=presentations,
            )
        executed_flow_keys: set[str] = set()
        selected: list[str] = []
        for answer in routing.answers:
            selection = await self._find_direct_selection(
                unit_of_work=unit_of_work,
                user=user,
                now=now,
                matcher=_message_flow_key_matcher(answer.candidate.key),
            )
            if selection is None:
                continue
            selected.extend(
                await self._execute_answer_fragment(
                    selection,
                    answer,
                    user=user,
                    incoming=incoming,
                    unit_of_work=unit_of_work,
                    correlation_id=correlation_id,
                    now=now,
                    presentations=presentations,
                )
            )

        for deferred in routing.deferred:
            await PendingIntentService(unit_of_work.pending_intents).enqueue(
                user_id=user.id,
                flow_key=deferred.key,
                flow_version_id=_required_flow_version_id(deferred),
                service_id=deferred.service_id,
                now=now,
            )

        if routing.interactive is not None:
            selection = await self._find_direct_selection(
                unit_of_work=unit_of_work,
                user=user,
                now=now,
                matcher=_message_flow_key_matcher(routing.interactive.candidate.key),
            )
            if selection is not None:
                interactive_execution = await self._execute_selection(
                    selection,
                    user=user,
                    incoming=incoming,
                    unit_of_work=unit_of_work,
                    correlation_id=correlation_id,
                    now=now,
                    executed_flow_keys=executed_flow_keys,
                    presentations=presentations,
                    reply_plan=routing.interactive.reply_plan,
                )
                selected.extend(interactive_execution.executed_flow_keys)
                if interactive_execution.completed:
                    selected.extend(
                        await self._resume_one_pending_intent(
                            user=user,
                            incoming=incoming,
                            unit_of_work=unit_of_work,
                            correlation_id=correlation_id,
                            now=now,
                            executed_flow_keys=executed_flow_keys,
                            presentations=presentations,
                        )
                    )

        if routing.terminal is RoutingTerminal.CLARIFY:
            self._append_fixed_text(
                presentations,
                user,
                "could you tell me a bit more about what you'd like to know?",
            )
            return self._result("clarified", tuple(selected), presentations)
        if routing.terminal is RoutingTerminal.NO_MATCH:
            self._append_fixed_text(
                presentations,
                user,
                text=(
                    self._routing_policy.no_match_text or "sorry, what did you mean?"
                ),
            )
            return self._result("no_match", tuple(selected), presentations)
        return self._result("selected", tuple(selected), presentations)

    async def _dispatch_onboarding(
        self,
        *,
        user: UserRecord,
        incoming: TelegramMessage,
        unit_of_work: UnitOfWork,
        correlation_id: UUID,
        now: datetime,
        presentations: PresentationBuffer,
    ) -> DispatchResult | None:
        """Handle deterministic onboarding before provider-backed message routing."""

        if self._onboarding is None:
            return None
        if normalize_command(incoming.text or "") in {
            "/login",
            "/manage",
            "/logout",
        }:
            return None
        result = await self._onboarding.handle_in_uow(
            unit_of_work,
            user_id=user.id,
            message=incoming,
            now=now,
        )
        if result.kind == "existing":
            return None
        if result.kind == "ignored":
            return DispatchResult("ignored")
        if result.kind == "name_capture":
            self._append_fixed_text(
                presentations,
                user,
                "Hey! Welcome to The Zone! Glad to see you here today!\n\n"
                "How may I address you?",
            )
            return DispatchResult("onboarding")
        if result.kind == "existing_start":
            await self.open_system_root_for_user(
                user_id=user.id,
                unit_of_work=unit_of_work,
                now=now,
                presentations=presentations,
            )
            return DispatchResult("onboarding")

        if result.kind != "name_captured":
            raise AssertionError("onboarding returned an unsupported outcome")
        completed_user = await unit_of_work.users.require_by_id(user.id)
        attendance = await self._dependencies.services.resolve_for_new_nbnc_in_uow(
            unit_of_work,
            user_id=completed_user.id,
            services=(self._zone_x.service,),
            now=now,
        )
        if attendance.kind == "selected":
            await self._open_root(
                user=completed_user,
                incoming=incoming,
                unit_of_work=unit_of_work,
                version=self._zone_x.service_root,
                root=_root_from_version(self._zone_x.service_root),
                service=self._zone_x.service,
                now=now,
                run_actions=True,
                presentations=presentations,
            )
        elif attendance.kind == "latecomer":
            await self._open_root(
                user=completed_user,
                incoming=incoming,
                unit_of_work=unit_of_work,
                version=self._zone_x.latecomer_root,
                root=_root_from_version(self._zone_x.latecomer_root),
                service=self._zone_x.service,
                now=now,
                run_actions=True,
                presentations=presentations,
            )
        else:
            await self.open_system_root_for_user(
                user_id=completed_user.id,
                unit_of_work=unit_of_work,
                now=now,
                presentations=presentations,
            )
        return DispatchResult("onboarding")

    async def open_system_root_for_user(
        self,
        *,
        user_id: UUID,
        unit_of_work: UnitOfWork,
        now: datetime,
        presentations: PresentationBuffer | None = None,
    ) -> OpenSelectionState:
        """Provision the Zone X system checkpoint without starting another UoW."""

        user = await unit_of_work.users.require_by_id(user_id)
        branch, _ = await self._open_root(
            user=user,
            incoming=None,
            unit_of_work=unit_of_work,
            version=self._zone_x.system_root,
            root=_root_from_version(self._zone_x.system_root),
            service=None,
            now=now,
            run_actions=True,
            presentations=presentations,
            reset_global_root=True,
        )
        return branch

    async def open_timestamp_root_for_user(
        self,
        *,
        timestamp: ServiceTimestampRecord,
        user_id: UUID,
        unit_of_work: UnitOfWork,
        now: datetime,
    ) -> OpenSelectionState:
        """Open and execute one independently scoped timestamp root for a recipient."""

        if timestamp.service_id != self._zone_x.service.id:
            raise ValueError("timestamp does not belong to the composed Zone X service")
        user = await unit_of_work.users.require_by_id(user_id)
        version = await unit_of_work.flow_versions.get(timestamp.flow_version_id)
        branch, _ = await self._open_root(
            user=user,
            incoming=None,
            unit_of_work=unit_of_work,
            version=version,
            root=_root_from_version(version),
            service=self._zone_x.service,
            now=now,
            run_actions=True,
        )
        return branch

    async def open_for_recipient(
        self,
        unit_of_work: UnitOfWork,
        timestamp: ServiceTimestampRecord,
        user_id: UUID,
        *,
        now: datetime,
    ) -> TimestampRootPreparation:
        """Satisfy T02's timestamp port with all runner-owned durable outputs."""

        if timestamp.service_id != self._zone_x.service.id:
            raise ValueError("timestamp does not belong to the composed Zone X service")
        user = await unit_of_work.users.require_by_id(user_id)
        version = await unit_of_work.flow_versions.get(timestamp.flow_version_id)
        presentations = PresentationBuffer()
        await self._open_root(
            user=user,
            incoming=None,
            unit_of_work=unit_of_work,
            version=version,
            root=_root_from_version(version),
            service=self._zone_x.service,
            now=now,
            run_actions=True,
            presentations=presentations,
        )
        return TimestampRootPreparation(presentations.snapshot())

    async def _dispatch_callback(
        self,
        *,
        user: UserRecord,
        incoming: TelegramMessage,
        unit_of_work: UnitOfWork,
        correlation_id: UUID,
        now: datetime,
        presentations: PresentationBuffer,
    ) -> DispatchResult:
        callback = incoming.callback
        assert callback is not None
        try:
            service, request, expired = await self._revalidate_callback_context(
                callback, user=user, unit_of_work=unit_of_work, now=now
            )
        except LookupError:
            return DispatchResult("ignored")
        if expired:
            self._append_fixed_text(presentations, user, "Sorry, the service is over!")
            return DispatchResult("expired")
        selection = await self._find_direct_selection(
            unit_of_work=unit_of_work,
            user=user,
            now=now,
            matcher=lambda child: _matches_button(child.trigger, callback.button_id),
            service=service,
            match_request=request,
        )
        if selection is None:
            return DispatchResult("ignored")
        executed = await self._execute_selection(
            selection,
            user=user,
            incoming=incoming,
            unit_of_work=unit_of_work,
            correlation_id=correlation_id,
            now=now,
            executed_flow_keys=set(),
            presentations=presentations,
        )
        return DispatchResult("selected", executed.executed_flow_keys)

    async def _revalidate_callback_context(
        self,
        callback: TelegramCallback,
        *,
        user: UserRecord,
        unit_of_work: UnitOfWork,
        now: datetime,
    ) -> tuple[ServiceRecord | None, MatchRequestRecord | None, bool]:
        if callback.context_kind is None:
            return None, None, False
        assert callback.context_id is not None
        if callback.context_kind is TelegramCallbackContextKind.SERVICE:
            service = await unit_of_work.services.get(callback.context_id)
            return service, None, now >= service.interaction_ends_at
        if callback.context_kind is TelegramCallbackContextKind.MATCH_REQUEST:
            request = await unit_of_work.matches.require_request(
                callback.context_id, requester_user_id=user.id
            )
            if request.service_id is None:
                return None, request, False
            service = await unit_of_work.services.get(request.service_id)
            return service, request, now >= service.interaction_ends_at
        raise ValueError("Telegram callback context kind is unsupported")

    async def _find_direct_selection(
        self,
        *,
        unit_of_work: UnitOfWork,
        user: UserRecord,
        now: datetime,
        matcher: Callable[[DiscussionFlow], bool],
        service: ServiceRecord | None = None,
        match_request: MatchRequestRecord | None = None,
        skip_invalid_branches: bool = False,
    ) -> _DirectSelection | None:
        matches: list[_DirectSelection] = []
        for branch in await self._valid_branches(unit_of_work, user.id, now=now):
            if (
                match_request is not None
                and match_request.service_id is not None
                and branch.service_id != match_request.service_id
            ):
                continue
            try:
                version = await unit_of_work.flow_versions.get(branch.flow_version_id)
            except LookupError:
                if skip_invalid_branches:
                    continue
                raise
            root = _root_from_version(version)
            parent = _find_flow(root, branch.parent_flow_key)
            if parent is None:
                if skip_invalid_branches:
                    continue
                raise ValueError(
                    "open selection parent is absent from its flow version"
                )
            for child in parent.next_flows:
                if not matcher(child):
                    continue
                if (
                    service is not None
                    and branch.service_id is not None
                    and branch.service_id != service.id
                    and not any(
                        isinstance(action, SelectServiceAttendanceAction)
                        for action in child.actions
                    )
                ):
                    continue
                matches.append(
                    _DirectSelection(
                        branch=branch,
                        version=version,
                        root=root,
                        parent=parent,
                        child=child,
                        service=service,
                        match_request=match_request,
                    )
                )
        if len(matches) != 1:
            return None
        return matches[0]

    async def _valid_branches(
        self, unit_of_work: UnitOfWork, user_id: UUID, *, now: datetime
    ) -> tuple[OpenSelectionState, ...]:
        branches: list[OpenSelectionState] = []
        for branch in await unit_of_work.open_selections.list_for_user(
            user_id, now=now
        ):
            if branch.service_id is None:
                branches.append(branch)
                continue
            service = await unit_of_work.services.get(branch.service_id)
            if now < service.interaction_ends_at:
                branches.append(branch)
        return tuple(branches)

    async def _execute_selection(
        self,
        selection: _DirectSelection,
        *,
        user: UserRecord,
        incoming: TelegramMessage,
        unit_of_work: UnitOfWork,
        correlation_id: UUID,
        now: datetime,
        executed_flow_keys: set[str],
        presentations: PresentationBuffer,
        reply_plan: ReplyPlan | None = None,
    ) -> _ExecutedInteractive:
        engine = SelectionTransitionEngine(
            executed_flow_keys=executed_flow_keys,
            flow_definitions=_flow_index(selection.root),
        )
        transition = engine.select_child(
            parent=selection.branch,
            child=selection.child,
            parent_definition=selection.parent,
            now=now,
        )
        context = self._context_for(
            user=user,
            incoming=incoming,
            flow=selection.child,
            version=selection.version,
            branch=selection.branch,
            unit_of_work=unit_of_work,
            correlation_id=correlation_id,
            now=now,
            service=selection.service,
            match_request=selection.match_request,
            reply_plan=(
                reply_plan
                if reply_plan is not None
                else await self._plan_known_flow(
                    selection.child,
                    incoming=incoming,
                    unit_of_work=unit_of_work,
                    correlation_id=correlation_id,
                    checkpoint=_checkpoint_for_selection(selection),
                )
            ),
            presentations=presentations,
        )
        result = await self._runner.run(selection.child, context)
        await unit_of_work.open_selections.apply(transition, at=now)
        await self._run_checkpoint_return(transition.checkpoint_return, context)
        await self._apply_event_selection_transitions(
            executed=result.executed_flow_keys,
            initial_child=selection.child,
            initial_branch=(
                transition.upsert_selections[0]
                if transition.upsert_selections
                else selection.branch
            ),
            root=selection.root,
            context=context,
            unit_of_work=unit_of_work,
            engine=engine,
            now=now,
        )
        return _ExecutedInteractive(
            result.executed_flow_keys,
            result.completed and transition.checkpoint_return is not None,
        )

    async def _execute_answer_fragment(
        self,
        selection: _DirectSelection,
        match: RoutedMatch,
        *,
        user: UserRecord,
        incoming: TelegramMessage,
        unit_of_work: UnitOfWork,
        correlation_id: UUID,
        now: datetime,
        presentations: PresentationBuffer,
    ) -> tuple[str, ...]:
        """Run an answer flow as a presentation-only fragment with no state change."""

        context = self._context_for(
            user=user,
            incoming=incoming,
            flow=selection.child,
            version=selection.version,
            branch=selection.branch,
            unit_of_work=unit_of_work,
            correlation_id=correlation_id,
            now=now,
            service=selection.service,
            match_request=selection.match_request,
            reply_plan=match.reply_plan,
            presentations=presentations,
        )
        return (await self._runner.run(selection.child, context)).executed_flow_keys

    async def _resume_one_pending_intent(
        self,
        *,
        user: UserRecord,
        incoming: TelegramMessage,
        unit_of_work: UnitOfWork,
        correlation_id: UUID,
        now: datetime,
        executed_flow_keys: set[str],
        presentations: PresentationBuffer,
    ) -> tuple[str, ...]:
        """Resume at most one still-valid interactive reference after completion."""

        intents = PendingIntentService(unit_of_work.pending_intents)
        for intent in await intents.list_active(user.id, now=now):
            selection = await self._selection_for_pending_intent(
                intent, user=user, unit_of_work=unit_of_work, now=now
            )
            if selection is None:
                await intents.remove(intent.id)
                continue
            await intents.remove(intent.id)
            resumed = await self._execute_selection(
                selection,
                user=user,
                incoming=incoming,
                unit_of_work=unit_of_work,
                correlation_id=correlation_id,
                now=now,
                executed_flow_keys=executed_flow_keys,
                presentations=presentations,
            )
            return resumed.executed_flow_keys
        return ()

    async def _selection_for_pending_intent(
        self,
        intent: PendingFlowIntentRecord,
        *,
        user: UserRecord,
        unit_of_work: UnitOfWork,
        now: datetime,
    ) -> _DirectSelection | None:
        selection = await self._find_direct_selection(
            unit_of_work=unit_of_work,
            user=user,
            now=now,
            matcher=_message_flow_key_matcher(intent.flow_key),
        )
        if selection is None:
            return None
        if (
            selection.version.id != intent.flow_version_id
            or selection.branch.service_id != intent.service_id
        ):
            return None
        return selection

    async def _apply_event_selection_transitions(
        self,
        *,
        executed: tuple[str, ...],
        initial_child: DiscussionFlow,
        initial_branch: OpenSelectionState,
        root: DiscussionFlow,
        context: ActionContext,
        unit_of_work: UnitOfWork,
        engine: SelectionTransitionEngine,
        now: datetime,
    ) -> None:
        """Persist event-child branches after their runner-owned actions complete."""

        parent = initial_child
        branch = initial_branch
        for key in executed[1:]:
            child = _direct_action_event_child(parent, key)
            if child is None:
                raise RuntimeError("action runner executed a non-direct event child")
            transition = engine.select_child(
                parent=branch,
                child=child,
                parent_definition=parent,
                now=now,
            )
            await unit_of_work.open_selections.apply(transition, at=now)
            await self._run_checkpoint_return(transition.checkpoint_return, context)
            parent = child
            if not transition.upsert_selections:
                break
            branch = transition.upsert_selections[0]

    async def _run_checkpoint_return(
        self, transition: object, context: ActionContext
    ) -> None:
        if transition is None:
            return
        return_actions = getattr(transition, "return_actions", None)
        if type(return_actions) is not tuple or not return_actions:
            return
        return_flow = DiscussionFlow(
            key="runtime.checkpoint.return",
            next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
            actions=list(return_actions),
        )
        await self._runner.run(
            return_flow, context.for_child(return_flow, event_payload=None)
        )

    async def _plan_known_flow(
        self,
        flow: DiscussionFlow,
        *,
        incoming: TelegramMessage | None,
        unit_of_work: UnitOfWork,
        correlation_id: UUID,
        checkpoint: DiscussionFlow | None,
    ) -> ReplyPlan:
        """Plan a deterministic flow's replies or fall back only to authored copy."""

        response_plan = plan_candidate_responses(flow, checkpoint=checkpoint)
        if not response_plan.reply_slots:
            return ReplyPlan(())
        messages = (incoming.text,) if incoming is not None and incoming.text else ()
        try:
            match = await self._router.plan_known_flow(
                KnownFlowRequest(
                    flow_id=str(flow.key),
                    reply_slots=response_plan.reply_slots,
                    messages=messages,
                )
            )
            return _reply_plan_from_known_match(match, response_plan)
        except (GatewayTransportError, GatewayProtocolError, ValueError):
            await unit_of_work.diagnostics.record(
                correlation_id=correlation_id,
                severity="warning",
                safe_summary="known-flow LLM reply unavailable",
                safe_context={"reason_code": "llm_reply.known_flow_fallback"},
                at=(incoming.sent_at if incoming is not None else datetime.now(UTC)),
            )
            return ReplyPlan(
                (),
                frozenset(
                    (binding.flow_key, binding.action_index)
                    for binding in response_plan.bindings
                    if binding.mode == "paraphrased"
                ),
            )

    @staticmethod
    def _with_presentations(
        result: DispatchResult, presentations: PresentationBuffer
    ) -> DispatchResult:
        return replace(result, presentations=presentations.snapshot())

    def _result(
        self,
        kind: DispatchKind,
        executed_flow_keys: tuple[str, ...],
        presentations: PresentationBuffer,
    ) -> DispatchResult:
        return DispatchResult(kind, executed_flow_keys, presentations.snapshot())

    def _context_for(
        self,
        *,
        user: UserRecord,
        incoming: TelegramMessage | None,
        flow: DiscussionFlow,
        version: FlowVersionRecord,
        branch: OpenSelectionState,
        unit_of_work: UnitOfWork,
        correlation_id: UUID,
        now: datetime,
        service: ServiceRecord | None,
        match_request: MatchRequestRecord | None,
        reply_plan: ReplyPlan | None = None,
        presentations: PresentationBuffer | None = None,
    ) -> ActionContext:
        selected_service = service
        if selected_service is None and branch.service_id == self._zone_x.service.id:
            selected_service = self._zone_x.service
        local_values: dict[str, JsonValue] = {}
        if selected_service is not None:
            local_values["service.id"] = str(selected_service.id)
            local_values["service.name"] = selected_service.name
            if selected_service.id == self._zone_x.service.id:
                local_values["service.map_url"] = self._zone_x.map_url
        context = ActionContext(
            user=user,
            incoming=incoming,
            flow=flow,
            flow_version=version,
            branch=branch,
            unit_of_work=unit_of_work,
            now=now,
            correlation_id=correlation_id,
            local_values=local_values,
            telegram=self._dependencies.telegram,
            services=self._dependencies.services,
            lifecycle=self._dependencies.lifecycle,
            matching=self._dependencies.matching,
            diagnostics=unit_of_work.diagnostics,
            operational_accounts=self._dependencies.operational_accounts,
            navigation=self._navigation,
            reply_plan=reply_plan or ReplyPlan(()),
            presentation_buffer=presentations or PresentationBuffer(),
        )
        if match_request is not None:
            context.set_match_request(match_request)
        return context

    async def _open_root(
        self,
        *,
        user: UserRecord,
        incoming: TelegramMessage | None,
        unit_of_work: UnitOfWork,
        version: FlowVersionRecord,
        root: DiscussionFlow,
        service: ServiceRecord | None,
        now: datetime,
        run_actions: bool,
        reset_global_root: bool = False,
        parent_context: ActionContext | None = None,
        presentations: PresentationBuffer | None = None,
    ) -> tuple[OpenSelectionState, int]:
        await unit_of_work.lock_user(user.id)
        root_selection = _root_selection(user=user, version=version, root=root, now=now)
        branch = (
            await unit_of_work.open_selections.reset_global_root(root_selection, at=now)
            if reset_global_root
            else await unit_of_work.open_selections.open_root(root_selection, at=now)
        )
        if not run_actions:
            return branch, 0
        if parent_context is None:
            context = self._context_for(
                user=user,
                incoming=incoming,
                flow=root,
                version=version,
                branch=branch,
                unit_of_work=unit_of_work,
                correlation_id=uuid4(),
                now=now,
                service=service,
                match_request=None,
                reply_plan=await self._plan_known_flow(
                    root,
                    incoming=incoming,
                    unit_of_work=unit_of_work,
                    correlation_id=uuid4(),
                    checkpoint=None,
                ),
                presentations=presentations,
            )
        else:
            context = parent_context.for_child(root, event_payload=None)
            context.flow_version = version
            context.branch = branch
            if service is not None:
                context.set_local_value("service.id", str(service.id))
                context.set_local_value("service.name", service.name)
                if service.id == self._zone_x.service.id:
                    context.set_local_value("service.map_url", self._zone_x.map_url)
        delivery_count_before = context.queued_delivery_count
        await self._runner.run(root, context)
        return branch, context.queued_delivery_count - delivery_count_before

    def _append_fixed_text(
        self, presentations: PresentationBuffer, user: UserRecord, text: str
    ) -> None:
        if type(user.telegram_user_id) is not int or user.telegram_user_id <= 0:
            raise ValueError("a Telegram user id is required for a presentation")
        presentations.append(TelegramTextPresentation(user.telegram_user_id, text))

    async def _routing_failure_result(
        self,
        unit_of_work: UnitOfWork,
        *,
        user: UserRecord,
        correlation_id: UUID,
        now: datetime,
        reason_code: str,
        error: Exception,
        presentations: PresentationBuffer,
    ) -> DispatchResult:
        LOGGER.error("routing provider failure: %s", reason_code)
        error_log_path = write_error_log(
            error,
            summary="routing provider failed",
            context={"reason_code": reason_code},
        )
        await unit_of_work.diagnostics.record(
            correlation_id=correlation_id,
            severity="error",
            safe_summary="routing provider failed",
            safe_context={"reason_code": reason_code},
            at=now,
        )
        self._append_fixed_text(
            presentations,
            user,
            DEFAULT_UNHANDLED_ERROR_TEXT.format(
                error_log_path=error_log_name(error_log_path)
            ),
        )
        return self._result("failed", (), presentations)


class _ApplicationNavigation(ActionNavigation):
    """The action port that maps only approved Zone X navigation to F01 state."""

    def __init__(self, application: FriendlyBotApplication) -> None:
        self._application = application

    async def enter_service_checkpoint(
        self, context: ActionContext, *, flow_key: str
    ) -> None:
        if flow_key != self._application._zone_x.service_root.root_flow_key:
            raise ValueError(
                "configured service checkpoint is not published for Zone X"
            )
        service = await context.unit_of_work.services.get(context.selected_service_id())
        if service.id != self._application._zone_x.service.id:
            raise ValueError("selected service has no composed Zone X checkpoint")
        await self._application._open_root(
            user=context.user,
            incoming=context.incoming,
            unit_of_work=context.unit_of_work,
            version=self._application._zone_x.service_root,
            root=_root_from_version(self._application._zone_x.service_root),
            service=service,
            now=context.now,
            run_actions=True,
            parent_context=context,
        )

    async def enter_selected_service_checkpoint(self, context: ActionContext) -> None:
        await self.enter_service_checkpoint(
            context,
            flow_key=self._application._zone_x.service_root.root_flow_key,
        )

    async def enter_selected_service_latecomer_flow(
        self, context: ActionContext
    ) -> None:
        service = await context.unit_of_work.services.get(context.selected_service_id())
        if service.id != self._application._zone_x.service.id:
            raise ValueError("selected service has no composed Zone X latecomer flow")
        await self._application._open_root(
            user=context.user,
            incoming=context.incoming,
            unit_of_work=context.unit_of_work,
            version=self._application._zone_x.latecomer_root,
            root=_root_from_version(self._application._zone_x.latecomer_root),
            service=service,
            now=context.now,
            run_actions=True,
            parent_context=context,
        )

    async def return_to_nearest_checkpoint(self, context: ActionContext) -> None:
        root = _root_from_version(context.flow_version)
        transition = SelectionTransitionEngine(
            executed_flow_keys=set(), flow_definitions=_flow_index(root)
        ).return_to_nearest_checkpoint(branch=context.branch, now=context.now)
        await context.unit_of_work.open_selections.apply(transition, at=context.now)
        await self._application._run_checkpoint_return(transition, context)

    async def return_users_to_system_checkpoint(
        self, context: ActionContext, *, user_ids: frozenset[UUID]
    ) -> None:
        for user_id in user_ids:
            user = await context.unit_of_work.users.require_by_id(user_id)
            await self._application._open_root(
                user=user,
                incoming=None,
                unit_of_work=context.unit_of_work,
                version=self._application._zone_x.system_root,
                root=_root_from_version(self._application._zone_x.system_root),
                service=None,
                now=context.now,
                run_actions=False,
            )


def _root_from_version(version: FlowVersionRecord) -> DiscussionFlow:
    """Decode the one immutable document attached to a persisted version."""

    return DiscussionFlow.model_validate(version.definition)


def _find_flow(root: DiscussionFlow, key: str) -> DiscussionFlow | None:
    """Resolve one stable flow key inside its immutable root document."""

    for flow_key, flow in _flow_index(root).items():
        if flow_key == key:
            return flow
    return None


def _checkpoint_for_selection(selection: _DirectSelection) -> DiscussionFlow | None:
    """Find the only checkpoint whose return actions can execute with a choice."""

    if selection.child.next_flow_mode is NextFlowMode.CHECKPOINT:
        return selection.child
    if selection.parent.next_flow_mode is NextFlowMode.CHECKPOINT:
        return selection.parent
    if not selection.branch.checkpoint_flow_keys:
        return None
    checkpoint = _find_flow(selection.root, selection.branch.checkpoint_flow_keys[-1])
    if checkpoint is None:
        raise ValueError("open selection checkpoint is absent from its flow version")
    return checkpoint


def _reply_plan_from_known_match(
    match: PlannedFlowMatch, response_plan: CandidateResponsePlan
) -> ReplyPlan:
    """Bind a validated known-flow model result to local action addresses."""

    replies = {reply.slot_id: reply.text for reply in match.replies}
    bindings = response_plan.bindings
    if match.flow_id != str(bindings[0].flow_key) or len(replies) != len(match.replies):
        raise ValueError("known-flow model response is invalid")
    if set(replies) != {binding.slot_id for binding in bindings}:
        raise ValueError("known-flow reply slots do not match the local flow")
    return ReplyPlan(
        tuple(
            PlannedActionText(
                binding.flow_key, binding.action_index, replies[binding.slot_id]
            )
            for binding in bindings
        )
    )


def _required_flow_version_id(candidate: object) -> UUID:
    flow_version_id = getattr(candidate, "flow_version_id", None)
    if not isinstance(flow_version_id, UUID):
        raise TypeError("deferred routing candidate is missing a flow version")
    return flow_version_id


def _flow_index(root: DiscussionFlow) -> dict[str, DiscussionFlow]:
    """Index each unique configured flow key without inventing a graph adapter."""

    index: dict[str, DiscussionFlow] = {}
    pending = [root]
    while pending:
        flow = pending.pop()
        key = str(flow.key)
        if key in index:
            raise ValueError("published flow definition repeats a stable flow key")
        index[key] = flow
        pending.extend(reversed(flow.next_flows))
    return index


def _root_selection(
    *,
    user: UserRecord,
    version: FlowVersionRecord,
    root: DiscussionFlow,
    now: datetime,
) -> OpenSelectionState:
    """Build the idempotent F01 state row for an automatically opened root."""

    root_key = str(root.key)
    if root_key != version.root_flow_key:
        raise ValueError("flow version root does not match its immutable document")
    is_checkpoint = root.next_flow_mode is NextFlowMode.CHECKPOINT
    return OpenSelectionState(
        id=uuid5(
            NAMESPACE_URL,
            f"friendly-bot:root:{user.id}:{version.id}:{root_key}",
        ),
        user_id=user.id,
        flow_version_id=version.id,
        parent_flow_key=root_key,
        service_id=version.service_id,
        is_current=True,
        is_global_interruptive=version.scope_kind is FlowScopeKind.SYSTEM,
        ancestor_flow_keys=(root_key,),
        checkpoint_flow_keys=(root_key,) if is_checkpoint else (),
        opened_at=now,
        last_focused_at=now,
    )


def _requires_system_root_reset(
    branches: tuple[OpenSelectionState, ...], *, current_version_id: UUID
) -> bool:
    """Replace a missing, duplicate, or stale system selection with the seed root."""

    system_branches = tuple(
        branch
        for branch in branches
        if branch.service_id is None and branch.is_global_interruptive
    )
    return (
        len(system_branches) != 1
        or system_branches[0].flow_version_id != current_version_id
    )


def _matches_button(trigger: DiscussionTrigger | None, button_id: str) -> bool:
    return any(
        isinstance(candidate, OnButtonPressTrigger) and candidate.button_id == button_id
        for candidate in _trigger_options(trigger)
    )


def _matches_command(trigger: DiscussionTrigger | None, command: str) -> bool:
    return any(
        isinstance(candidate, OnCommandTrigger) and candidate.command == command
        for candidate in _trigger_options(trigger)
    )


def _matches_message(trigger: DiscussionTrigger | None) -> bool:
    return any(
        isinstance(candidate, OnMessageTrigger)
        for candidate in _trigger_options(trigger)
    )


def _matches_any_message(trigger: DiscussionTrigger | None) -> bool:
    """Identify a local reply capture that deliberately bypasses provider routing."""

    return any(
        isinstance(candidate, OnAnyMessageTrigger)
        for candidate in _trigger_options(trigger)
    )


def _message_flow_key_matcher(key: str) -> Callable[[DiscussionFlow], bool]:
    """Bind one router decision to its exact message-trigger child."""

    return lambda child: str(child.key) == key and _matches_message(child.trigger)


def _trigger_options(
    trigger: DiscussionTrigger | None,
) -> tuple[object, ...]:
    if isinstance(trigger, OnAnyOfTrigger):
        return tuple(trigger.triggers)
    if trigger is None:
        return ()
    return (trigger,)


def _direct_action_event_child(
    parent: DiscussionFlow, key: str
) -> DiscussionFlow | None:
    """Resolve an already-run direct action-event child by its execution trace key."""

    matches = [
        child
        for child in parent.next_flows
        if str(child.key) == key and isinstance(child.trigger, OnActionEventTrigger)
    ]
    if len(matches) > 1:
        raise ValueError("published flow has duplicate direct action-event children")
    return matches[0] if matches else None


def load_zone_x_seed(path: Path) -> ZoneXSeed:
    """Decode one Zone X Python module; YAML is never a runtime input."""

    raw = load_seed_module(path, export_name="ZONE_X_SEED")
    return ZoneXSeed.model_validate(_normalize_seed_document(raw))


def load_system_global_seed(path: Path) -> SystemGlobalSeed:
    """Decode the one system-global Python module shared by every service."""

    raw = load_seed_module(path, export_name="SYSTEM_GLOBAL_SEED")
    return SystemGlobalSeed.model_validate(_normalize_seed_document(raw))


def load_seed_module(path: Path, *, export_name: str) -> dict[str, object]:
    """Load one named, JSON-safe object from a local Python seed module."""

    if not isinstance(path, Path):
        raise TypeError("seed module path must be a pathlib path")
    if path.suffix != ".py":
        raise SeedModuleLoadError("seed modules must use the .py extension")
    if not isinstance(export_name, str) or not export_name:
        raise TypeError("seed module export name must be a nonempty string")
    try:
        namespace = runpy.run_path(str(path))
    except Exception as exc:
        raise SeedModuleLoadError("could not load Python seed module") from exc
    raw = namespace.get(export_name)
    if not isinstance(raw, dict):
        raise SeedModuleLoadError("Python seed module must export an object")
    return cast(dict[str, object], raw)


async def publish_zone_x_seed(
    seed: ZoneXSeed, system_global_seed: SystemGlobalSeed, unit_of_work: UnitOfWork
) -> PublishedZoneX:
    """Validate and publish Zone X through F01's public immutable/version seed boundary."""

    if not isinstance(seed, ZoneXSeed):
        raise TypeError("Zone X seed is invalid")
    if not isinstance(system_global_seed, SystemGlobalSeed):
        raise TypeError("system-global seed is invalid")
    service = await unit_of_work.services.upsert(
        NewService(
            key=seed.service.key,
            name=seed.service.name,
            timezone=seed.service.timezone,
            highkey=seed.service.highkey,
            doors_open_at=seed.service.doors_open_at,
            doors_close_at=seed.service.doors_close_at,
            service_starts_at=seed.service.service_starts_at,
            service_ends_at=seed.service.service_ends_at,
            interaction_ends_at=seed.service.interaction_ends_at,
        )
    )
    for profile in system_global_seed.operational_profiles:
        await unit_of_work.operational_profiles.provision(
            display_name=profile.name,
            normalized_name=normalize_operational_name(profile.name),
            dob=profile.dob,
            role=profile.role,
            interests=list(profile.interests),
            cg_name=profile.cg_name,
            telegram_contact_url=profile.telegram_contact_url,
            always_available=profile.always_available,
            capacity=profile.capacity,
            is_admin=profile.is_admin,
            at=datetime.now(UTC),
        )
    system_root = await _publish_root(
        system_global_seed.root,
        unit_of_work=unit_of_work,
        scope_kind=FlowScopeKind.SYSTEM,
        service_id=None,
        root_kind=RootKind.SYSTEM,
    )
    service_root = await _publish_root(
        seed.service.service_global_root,
        unit_of_work=unit_of_work,
        scope_kind=FlowScopeKind.SERVICE,
        service_id=service.id,
        root_kind=RootKind.SERVICE,
    )
    latecomer_root = await _publish_root(
        seed.service.latecomer_flow,
        unit_of_work=unit_of_work,
        scope_kind=FlowScopeKind.SERVICE,
        service_id=service.id,
        root_kind=RootKind.TIMESTAMP,
    )
    timestamps: list[ServiceTimestampRecord] = []
    for timestamp in seed.service.timestamps:
        flow_version = await _publish_root(
            timestamp.root_flow,
            unit_of_work=unit_of_work,
            scope_kind=FlowScopeKind.TIMESTAMP,
            service_id=service.id,
            root_kind=RootKind.TIMESTAMP,
        )
        timestamps.append(
            await unit_of_work.services.upsert_timestamp(
                NewServiceTimestamp(
                    key=timestamp.key,
                    occurs_at=timestamp.occurs_at,
                    audience=timestamp.audience,
                    flow_version_id=flow_version.id,
                    root_flow_key=str(timestamp.root_flow.key),
                ),
                service_id=service.id,
            )
        )
    return PublishedZoneX(
        service=service,
        map_url=seed.service.map_url,
        system_root=system_root,
        service_root=service_root,
        latecomer_root=latecomer_root,
        timestamps=tuple(timestamps),
    )


async def _publish_root(
    root: DiscussionFlow,
    *,
    unit_of_work: UnitOfWork,
    scope_kind: FlowScopeKind,
    service_id: UUID | None,
    root_kind: RootKind,
) -> FlowVersionRecord:
    published: PublishedFlowDefinition = validate_for_publication(
        root, _ZONE_X_TEMPLATE_CONTEXT, root_kind
    )
    return await unit_of_work.flow_versions.publish(
        published,
        scope_kind=scope_kind,
        service_id=service_id,
        published_by_user_id=None,
    )


def _normalize_seed_document(value: object) -> object:
    """Translate only canonical YAML enum spellings to the existing F01 wire values."""

    if isinstance(value, list):
        return [_normalize_seed_document(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if key in {"next_flow_mode", "audience"} and isinstance(item, str):
            normalized[key] = item.lower()
        else:
            normalized[key] = _normalize_seed_document(item)
    return normalized


@dataclass(slots=True)
class FriendlyBotRuntime:
    """The one local process composition; no network operation starts at build time."""

    application: FriendlyBotApplication
    poller: TelegramPoller
    scheduler: ServiceDeliveryScheduler
    preflight: TelegramPreflight
    unit_of_work_factory: UnitOfWorkFactory
    _engine: AsyncEngine
    _telegram: TelegramApiClient
    _database_settings: DatabaseSettings

    async def aclose(self) -> None:
        """Close the owned transport and SQLAlchemy pool after all runtime loops stop."""

        try:
            await self._telegram.aclose()
        finally:
            await self._engine.dispose()


async def build_application(*, debug: bool = False) -> FriendlyBotRuntime:
    """Compose and idempotently publish Zone X without polling or sending anything."""

    database_settings = DatabaseSettings.model_validate({})
    telegram_settings = TelegramSettings.model_validate({})
    engine = create_async_engine(str(database_settings.database_url))
    session_factory = async_sessionmaker[AsyncSession](engine, expire_on_commit=False)
    unit_of_work_factory: UnitOfWorkFactory = lambda: UnitOfWork(session_factory)
    telegram = TelegramApiClient(telegram_settings.telegram_bot_token)
    try:
        router_gateway = OpenRouterGateway.from_environment(debug=debug)
        services = ServiceAttendanceService(unit_of_work_factory)
        onboarding = OnboardingService(unit_of_work_factory)
        operational_accounts = OperationalAccountService(unit_of_work_factory)
        lifecycle = ServiceLifecycleService(unit_of_work_factory)
        matching = MatchingService(
            unit_of_work_factory,
            router_gateway,
            _unexpected_standalone_match_requester,
        )
        registry = build_action_registry(
            ActionDependencies(
                telegram=telegram,
                services=services,
                lifecycle=lifecycle,
                matching=matching,
                operational_accounts=operational_accounts,
            )
        )
        system_global_seed = load_system_global_seed(
            PROJECT_ROOT / "seeds" / "system_global.py"
        )
        seed = load_zone_x_seed(PROJECT_ROOT / "seeds" / "services" / "zone_x.py")
        async with unit_of_work_factory() as unit_of_work:
            zone_x = await publish_zone_x_seed(seed, system_global_seed, unit_of_work)
        application = FriendlyBotApplication(
            dependencies=ActionDependencies(
                telegram=telegram,
                services=services,
                lifecycle=lifecycle,
                matching=matching,
                operational_accounts=operational_accounts,
            ),
            registry=registry,
            router=ConstrainedRouter(unit_of_work_factory, router_gateway),
            zone_x=zone_x,
            onboarding=onboarding,
        )
        sender = BestEffortTelegramSender(
            telegram,
            asset_resolver=LocalTelegramAssetResolver(
                PROJECT_ROOT / "assets", PROJECT_ROOT / "assets" / "catalog.json"
            ),
            logger=LOGGER,
        )
        ingress = TelegramIngress(unit_of_work_factory, application, sender)
        return FriendlyBotRuntime(
            application=application,
            poller=TelegramPoller(
                telegram,
                ingress,
                unit_of_work_factory,
                timeout_seconds=30,
            ),
            scheduler=ServiceDeliveryScheduler(
                unit_of_work_factory,
                AudienceResolver(unit_of_work_factory),
                application,
                sender,
            ),
            preflight=TelegramPreflight(telegram),
            unit_of_work_factory=unit_of_work_factory,
            _engine=engine,
            _telegram=telegram,
            _database_settings=database_settings,
        )
    except BaseException:
        await telegram.aclose()
        await engine.dispose()
        raise


async def run_application(*, debug: bool = False) -> None:
    """Run the sole local polling and scheduler process until stopped."""

    runtime = await build_application(debug=debug)
    try:
        await runtime.preflight.ensure_polling_ready()
        runtime_lock = await TelegramRuntimeLock.acquire(
            DirectPostgresConnectionFactory(runtime._database_settings.database_url)
        )
        async with runtime_lock:
            stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()
            installed_signals = _install_stop_signals(loop, stop_event)
            try:
                try:
                    async with asyncio.TaskGroup() as task_group:
                        task_group.create_task(
                            _poll_forever(runtime, runtime_lock, stop_event)
                        )
                        task_group.create_task(
                            _schedule_forever(runtime, runtime_lock, stop_event)
                        )
                        task_group.create_task(_raise_when_stopped(stop_event))
                except* _RuntimeStopped:
                    pass
            finally:
                for received_signal in installed_signals:
                    loop.remove_signal_handler(received_signal)
    finally:
        await runtime.aclose()


class _RuntimeStopped(Exception):
    """Internal TaskGroup signal used to cancel the three runtime loops cleanly."""


async def _poll_forever(
    runtime: FriendlyBotRuntime,
    runtime_lock: TelegramRuntimeLock,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        await runtime_lock.ensure_healthy()
        result = await runtime.poller.run_once(now=datetime.now(UTC))
        if result.gateway_failure is not None:
            _log_telegram_poll_failure(result.gateway_failure)
            await _wait_for_stop(stop_event, seconds=0.5)


async def _schedule_forever(
    runtime: FriendlyBotRuntime,
    runtime_lock: TelegramRuntimeLock,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        await runtime_lock.ensure_healthy()
        await runtime.scheduler.run_once(now=datetime.now(UTC))
        await _wait_for_stop(stop_event, seconds=30.0)


async def _wait_for_stop(stop_event: asyncio.Event, *, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        return


def _log_telegram_poll_failure(
    failure: TelegramApiError | TelegramResponseUncertain,
) -> None:
    """Emit a stable failure category without retaining Telegram payloads."""

    if isinstance(failure, TelegramApiError):
        LOGGER.warning(
            "telegram polling failure: api_error status=%s", failure.error_code
        )
        return
    LOGGER.warning("telegram polling failure: response_uncertain code=%s", failure.code)


async def _raise_when_stopped(stop_event: asyncio.Event) -> None:
    await stop_event.wait()
    raise _RuntimeStopped()


def _install_stop_signals(
    loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event
) -> tuple[signal.Signals, ...]:
    installed: list[signal.Signals] = []
    for received_signal in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(received_signal, stop_event.set)
            installed.append(received_signal)
    return tuple(installed)


def _unexpected_standalone_match_requester(request_id: UUID) -> UUID:
    """Prevent composition from silently opening a second UoW for ingress matching."""

    del request_id
    raise RuntimeError("I04 matching must run through its caller-owned unit of work")
