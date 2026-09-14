"""I04 seed publication and runtime composition primitives."""

from __future__ import annotations

import asyncio
import json
import signal
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal
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
    ActionEventDiscussionFlowTrigger,
    AnyOfDiscussionFlowTrigger,
    ButtonDiscussionFlowTrigger,
    CommandDiscussionFlowTrigger,
    DiscussionFlowTrigger,
    MessageDiscussionFlowTrigger,
)
from friendly_bot.matching.service import MatchingService
from friendly_bot.onboarding.service import OnboardingService
from friendly_bot.persistence.connection import DirectPostgresConnectionFactory
from friendly_bot.persistence.models import FlowScopeKind, ServiceAudience
from friendly_bot.persistence.repositories import (
    FlowVersionRecord,
    MatchRequestRecord,
    NewOutboundDelivery,
    NewService,
    NewServiceTimestamp,
    ServiceRecord,
    ServiceTimestampRecord,
    UserRecord,
)
from friendly_bot.persistence.uow import UnitOfWork, UnitOfWorkFactory
from friendly_bot.routing.openrouter_gateway import (
    GatewayProtocolError,
    GatewayTransportError,
    OpenRouterGateway,
)
from friendly_bot.routing.router import ConstrainedRouter, IncomingText, RoutingTerminal
from friendly_bot.services import (
    AudienceResolver,
    ServiceAttendanceService,
    ServiceDeliveryScheduler,
    ServiceLifecycleService,
)
from friendly_bot.services.scheduler import TimestampRootPreparation
from friendly_bot.telegram import (
    LocalTelegramAssetResolver,
    OutboundDeliveryWorker,
    TelegramApiClient,
    TelegramCallback,
    TelegramCallbackContextKind,
    TelegramIngress,
    TelegramMessage,
    TelegramPoller,
    TelegramPreflight,
    TelegramRuntimeLock,
    normalize_command,
)

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


class ZoneXTimestampSeed(BaseModel):
    """One canonical timestamp root decoded from the JSON source of truth."""

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
    """The complete canonical Zone X JSON document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_global_root_excerpt: DiscussionFlow
    service: ZoneXServiceSeed


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
    """One ingress decision without carrying Telegram content out of the UoW."""

    kind: Literal[
        "selected",
        "no_match",
        "clarified",
        "ignored",
        "expired",
        "failed",
        "onboarding",
    ]
    selected_flow_keys: tuple[str, ...] = ()


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

        if incoming.callback is not None:
            return await self._dispatch_callback(
                user=user,
                incoming=incoming,
                unit_of_work=unit_of_work,
                correlation_id=correlation_id,
                now=now,
            )

        assert incoming.text is not None
        onboarding = await self._dispatch_onboarding(
            user=user,
            incoming=incoming,
            unit_of_work=unit_of_work,
            correlation_id=correlation_id,
            now=now,
        )
        if onboarding is not None:
            return onboarding
        if not await self._valid_branches(unit_of_work, user.id, now=now):
            await self.open_system_root_for_user(
                user_id=user.id, unit_of_work=unit_of_work, now=now
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
                )
                return DispatchResult("selected", executed)

        try:
            routing = await self._router.route_update_in_uow(
                unit_of_work,
                user_id=user.id,
                incoming=IncomingText(
                    body=incoming.text, replied_to_body=incoming.reply_text
                ),
                now=now,
            )
        except GatewayTransportError:
            return await self._routing_failure_result(
                unit_of_work,
                user=user,
                correlation_id=correlation_id,
                now=now,
                reason_code="routing.provider_unavailable",
            )
        except GatewayProtocolError:
            return await self._routing_failure_result(
                unit_of_work,
                user=user,
                correlation_id=correlation_id,
                now=now,
                reason_code="routing.provider_invalid_response",
            )
        executed_flow_keys: set[str] = set()
        selected: list[str] = []
        for decision in routing.selected_keys:
            selection = await self._find_direct_selection(
                unit_of_work=unit_of_work,
                user=user,
                now=now,
                matcher=_message_flow_key_matcher(decision.key),
            )
            if selection is None:
                continue
            selected.extend(
                await self._execute_selection(
                    selection,
                    user=user,
                    incoming=incoming,
                    unit_of_work=unit_of_work,
                    correlation_id=correlation_id,
                    now=now,
                    executed_flow_keys=executed_flow_keys,
                )
            )

        if routing.terminal is RoutingTerminal.CLARIFY:
            await self._enqueue_fixed_text(
                unit_of_work,
                user=user,
                correlation_id=correlation_id,
                now=now,
                text="Sorry, which message were you referring to?",
            )
            return DispatchResult("clarified", tuple(selected))
        if routing.terminal is RoutingTerminal.NO_MATCH:
            await self._enqueue_fixed_text(
                unit_of_work,
                user=user,
                correlation_id=correlation_id,
                now=now,
                text=(
                    self._routing_policy.no_match_text
                    or "Sorry, I didn't understand your request."
                ),
            )
            return DispatchResult("no_match", tuple(selected))
        return DispatchResult("selected", tuple(selected))

    async def _dispatch_onboarding(
        self,
        *,
        user: UserRecord,
        incoming: TelegramMessage,
        unit_of_work: UnitOfWork,
        correlation_id: UUID,
        now: datetime,
    ) -> DispatchResult | None:
        """Handle deterministic onboarding before provider-backed message routing."""

        if self._onboarding is None:
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
            await self._enqueue_fixed_text(
                unit_of_work,
                user=user,
                correlation_id=correlation_id,
                now=now,
                text=(
                    "Hey! Welcome to The Zone! Glad to see you here today!\n\n"
                    "How may I address you?"
                ),
            )
            return DispatchResult("onboarding")
        if result.kind == "existing_start":
            await self.open_system_root_for_user(
                user_id=user.id,
                unit_of_work=unit_of_work,
                now=now,
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
            )
        else:
            await self.open_system_root_for_user(
                user_id=completed_user.id,
                unit_of_work=unit_of_work,
                now=now,
            )
        return DispatchResult("onboarding")

    async def open_system_root_for_user(
        self, *, user_id: UUID, unit_of_work: UnitOfWork, now: datetime
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
        _, enqueued_delivery_count = await self._open_root(
            user=user,
            incoming=None,
            unit_of_work=unit_of_work,
            version=version,
            root=_root_from_version(version),
            service=self._zone_x.service,
            now=now,
            run_actions=True,
        )
        return TimestampRootPreparation(enqueued_delivery_count=enqueued_delivery_count)

    async def _dispatch_callback(
        self,
        *,
        user: UserRecord,
        incoming: TelegramMessage,
        unit_of_work: UnitOfWork,
        correlation_id: UUID,
        now: datetime,
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
            await self._enqueue_fixed_text(
                unit_of_work,
                user=user,
                correlation_id=correlation_id,
                now=now,
                text="Sorry, the service is over!",
            )
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
        )
        return DispatchResult("selected", executed)

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
    ) -> _DirectSelection | None:
        matches: list[_DirectSelection] = []
        for branch in await self._valid_branches(unit_of_work, user.id, now=now):
            if (
                match_request is not None
                and match_request.service_id is not None
                and branch.service_id != match_request.service_id
            ):
                continue
            version = await unit_of_work.flow_versions.get(branch.flow_version_id)
            root = _root_from_version(version)
            parent = _find_flow(root, branch.parent_flow_key)
            if parent is None:
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
    ) -> tuple[str, ...]:
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
        return result.executed_flow_keys

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
            navigation=self._navigation,
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
        parent_context: ActionContext | None = None,
    ) -> tuple[OpenSelectionState, int]:
        await unit_of_work.lock_user(user.id)
        branch = await unit_of_work.open_selections.open_root(
            _root_selection(user=user, version=version, root=root, now=now), at=now
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

    async def _enqueue_fixed_text(
        self,
        unit_of_work: UnitOfWork,
        *,
        user: UserRecord,
        correlation_id: UUID,
        now: datetime,
        text: str,
    ) -> None:
        if type(user.telegram_user_id) is not int or user.telegram_user_id <= 0:
            raise ValueError("a Telegram user id is required for outbound delivery")
        await unit_of_work.deliveries.enqueue(
            NewOutboundDelivery(
                idempotency_key=f"dispatch:{correlation_id}:terminal",
                user_id=user.id,
                telegram_chat_id=user.telegram_user_id,
                kind="message",
                payload={"text": text},
                eligible_at=now,
            )
        )

    async def _routing_failure_result(
        self,
        unit_of_work: UnitOfWork,
        *,
        user: UserRecord,
        correlation_id: UUID,
        now: datetime,
        reason_code: str,
    ) -> DispatchResult:
        diagnostic = await unit_of_work.diagnostics.record(
            correlation_id=correlation_id,
            severity="error",
            safe_summary="routing provider failed",
            safe_context={"reason_code": reason_code},
            at=now,
        )
        await unit_of_work.diagnostics.enqueue_admin_notifications(
            diagnostic.id, at=now
        )
        await self._enqueue_fixed_text(
            unit_of_work,
            user=user,
            correlation_id=correlation_id,
            now=now,
            text=DEFAULT_UNHANDLED_ERROR_TEXT.format(
                telegram_user_id=user.telegram_user_id
            ),
        )
        return DispatchResult("failed")


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


def _matches_button(trigger: DiscussionFlowTrigger | None, button_id: str) -> bool:
    return any(
        isinstance(candidate, ButtonDiscussionFlowTrigger)
        and candidate.button_id == button_id
        for candidate in _trigger_options(trigger)
    )


def _matches_command(trigger: DiscussionFlowTrigger | None, command: str) -> bool:
    return any(
        isinstance(candidate, CommandDiscussionFlowTrigger)
        and candidate.command == command
        for candidate in _trigger_options(trigger)
    )


def _matches_message(trigger: DiscussionFlowTrigger | None) -> bool:
    return any(
        isinstance(candidate, MessageDiscussionFlowTrigger)
        for candidate in _trigger_options(trigger)
    )


def _message_flow_key_matcher(key: str) -> Callable[[DiscussionFlow], bool]:
    """Bind one router decision to its exact message-trigger child."""

    return lambda child: str(child.key) == key and _matches_message(child.trigger)


def _trigger_options(
    trigger: DiscussionFlowTrigger | None,
) -> tuple[object, ...]:
    if isinstance(trigger, AnyOfDiscussionFlowTrigger):
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
        if str(child.key) == key
        and isinstance(child.trigger, ActionEventDiscussionFlowTrigger)
    ]
    if len(matches) > 1:
        raise ValueError("published flow has duplicate direct action-event children")
    return matches[0] if matches else None


def load_zone_x_seed(path: Path) -> ZoneXSeed:
    """Decode only the exact JSON seed shape; YAML is never a runtime configuration input."""

    if not isinstance(path, Path):
        raise TypeError("Zone X seed path must be a pathlib path")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ZoneXSeed.model_validate(_normalize_seed_document(raw))


async def publish_zone_x_seed(
    seed: ZoneXSeed, unit_of_work: UnitOfWork
) -> PublishedZoneX:
    """Validate and publish Zone X through F01's public immutable/version seed boundary."""

    if not isinstance(seed, ZoneXSeed):
        raise TypeError("Zone X seed is invalid")
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
    system_root = await _publish_root(
        seed.system_global_root_excerpt,
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
    outbox: OutboundDeliveryWorker
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


async def build_application() -> FriendlyBotRuntime:
    """Compose and idempotently publish Zone X without polling or sending anything."""

    database_settings = DatabaseSettings.model_validate({})
    telegram_settings = TelegramSettings.model_validate({})
    engine = create_async_engine(str(database_settings.database_url))
    session_factory = async_sessionmaker[AsyncSession](engine, expire_on_commit=False)
    unit_of_work_factory: UnitOfWorkFactory = lambda: UnitOfWork(session_factory)
    telegram = TelegramApiClient(telegram_settings.telegram_bot_token)
    try:
        router_gateway = OpenRouterGateway.from_environment()
        services = ServiceAttendanceService(unit_of_work_factory)
        onboarding = OnboardingService(unit_of_work_factory)
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
            )
        )
        seed = load_zone_x_seed(PROJECT_ROOT / "seeds" / "zone-x.json")
        async with unit_of_work_factory() as unit_of_work:
            zone_x = await publish_zone_x_seed(seed, unit_of_work)
        application = FriendlyBotApplication(
            dependencies=ActionDependencies(
                telegram=telegram,
                services=services,
                lifecycle=lifecycle,
                matching=matching,
            ),
            registry=registry,
            router=ConstrainedRouter(unit_of_work_factory, router_gateway),
            zone_x=zone_x,
            onboarding=onboarding,
        )
        ingress = TelegramIngress(unit_of_work_factory, application)
        return FriendlyBotRuntime(
            application=application,
            poller=TelegramPoller(
                telegram,
                ingress,
                unit_of_work_factory,
                timeout_seconds=30,
            ),
            outbox=OutboundDeliveryWorker(
                unit_of_work_factory,
                telegram,
                asset_resolver=LocalTelegramAssetResolver(
                    PROJECT_ROOT / "assets", PROJECT_ROOT / "assets" / "catalog.json"
                ),
            ),
            scheduler=ServiceDeliveryScheduler(
                unit_of_work_factory,
                AudienceResolver(unit_of_work_factory),
                application,
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


async def run_application() -> None:
    """Run the sole local polling, outbox, and scheduler process until stopped."""

    runtime = await build_application()
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
                            _outbox_forever(runtime, runtime_lock, stop_event)
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
            await _wait_for_stop(stop_event, seconds=0.5)


async def _outbox_forever(
    runtime: FriendlyBotRuntime,
    runtime_lock: TelegramRuntimeLock,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        await runtime_lock.ensure_healthy()
        sent = await runtime.outbox.run_once()
        if not sent:
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
