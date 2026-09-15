"""I04 dispatcher coverage for durable direct and contextual branch selection."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue

from friendly_bot.actions.context import ActionContext
from friendly_bot.actions.registry import ActionDependencies, ActionExecutorRegistry
from friendly_bot.app import DispatchResult, FriendlyBotApplication, PublishedZoneX
from friendly_bot.domain.actions import (
    SelectServiceAttendanceAction,
    SendMessageAction,
    SendMessageFixedAction,
)
from friendly_bot.domain.events import ActionEvent
from friendly_bot.domain.flows import DiscussionFlow
from friendly_bot.domain.state import (
    CheckpointReturnTransition,
    OpenSelectionState,
    SelectionTransition,
)
from friendly_bot.matching.service import MatchingService
from friendly_bot.onboarding.service import OnboardingService
from friendly_bot.persistence.models import (
    FlowScopeKind,
    OperationalRole,
    ServiceAudience,
)
from friendly_bot.persistence.repositories import (
    AttendanceRecord,
    AttendanceStartResult,
    DiagnosticRecord,
    DiagnosticRepository,
    FlowVersionRecord,
    MatchRequestRecord,
    NewPendingFlowIntent,
    PendingFlowIntentRecord,
    ServiceRecord,
    ServiceTimestampRecord,
    UserRecord,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.responses.planner import (
    CandidateResponsePlan,
    PlannedActionText,
    ReplyPlan,
)
from friendly_bot.routing.contracts import (
    KnownFlowRequest,
    PlannedFlowMatch,
    PlannedReply,
)
from friendly_bot.routing.openrouter_gateway import (
    GatewayProtocolError,
    GatewayTransportError,
)
from friendly_bot.routing.router import (
    ConstrainedRouter,
    MultiIntentRoutingResult,
    RoutedMatch,
    RoutingCandidate,
)
from friendly_bot.services import ServiceAttendanceService, ServiceLifecycleService
from friendly_bot.telegram import (
    TelegramCallback,
    TelegramCallbackContextKind,
    TelegramChat,
    TelegramGateway,
    TelegramMessage,
    TelegramTextPresentation,
    TelegramUser,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


@dataclass
class RecordingSelections:
    branches: list[OpenSelectionState]

    async def list_for_user(
        self, user_id: UUID, *, now: datetime
    ) -> list[OpenSelectionState]:
        del now
        return [branch for branch in self.branches if branch.user_id == user_id]

    async def apply(
        self,
        transition: SelectionTransition | CheckpointReturnTransition,
        *,
        at: datetime,
    ) -> None:
        del at
        if isinstance(transition, CheckpointReturnTransition):
            self._focus(transition.target_checkpoint_key)
            return
        self.branches = [
            branch
            for branch in self.branches
            if branch.id not in transition.delete_selection_ids
        ]
        self.branches = [
            replace(branch, is_current=False)
            if branch.id in transition.reusable_past_selection_ids
            else branch
            for branch in self.branches
        ]
        self.branches.extend(transition.upsert_selections)
        if transition.checkpoint_return is not None:
            self._focus(transition.checkpoint_return.target_checkpoint_key)

    async def open_root(
        self, root: OpenSelectionState, *, at: datetime
    ) -> OpenSelectionState:
        del at
        for branch in self.branches:
            if (
                branch.user_id == root.user_id
                and branch.flow_version_id == root.flow_version_id
                and branch.parent_flow_key == root.parent_flow_key
                and branch.service_id == root.service_id
            ):
                return branch
        self.branches.append(root)
        return root

    def _focus(self, target_key: str) -> None:
        self.branches = [
            replace(branch, is_current=branch.parent_flow_key == target_key)
            for branch in self.branches
        ]


@dataclass
class RecordingUsers:
    user: UserRecord

    async def require_by_id(self, user_id: UUID) -> UserRecord:
        if user_id != self.user.id:
            raise LookupError("user was not found")
        return self.user

    async def set_display_name(
        self, user_id: UUID, display_name: str, *, at: datetime
    ) -> UserRecord:
        if user_id != self.user.id:
            raise LookupError("user was not found")
        self.user = replace(self.user, display_name=display_name)
        return self.user


@dataclass
class RecordingServices:
    services: dict[UUID, ServiceRecord]

    async def get(self, service_id: UUID) -> ServiceRecord:
        try:
            return self.services[service_id]
        except KeyError as error:
            raise LookupError("service was not found") from error


@dataclass
class RecordingAttendances:
    started: list[tuple[UUID, UUID, str]] = field(default_factory=list)

    async def start_or_switch(
        self,
        user_id: UUID,
        service_id: UUID,
        *,
        attendee_kind: str,
        started_at: datetime,
    ) -> AttendanceStartResult:
        self.started.append((user_id, service_id, attendee_kind))
        return AttendanceStartResult(
            attendance=AttendanceRecord(
                id=uuid4(),
                service_id=service_id,
                user_id=user_id,
                attendee_kind=attendee_kind,
                started_at=started_at,
                ended_at=None,
            ),
            ended_previous=False,
        )


@dataclass
class RecordingConversations:
    messages: list[object] = field(default_factory=list)

    async def list_after(self, user_id: UUID, cursor: object) -> list[object]:
        del user_id, cursor
        return self.messages


class RejectingMatches:
    async def require_request(
        self, request_id: UUID, *, requester_user_id: UUID
    ) -> MatchRequestRecord:
        del request_id, requester_user_id
        raise LookupError("match request was not found")


class RecordingDiagnostics:
    reason_codes: list[str]

    def __init__(self) -> None:
        self.reason_codes = []

    async def record(self, **kwargs: object) -> DiagnosticRecord:
        safe_context = cast(dict[str, JsonValue], kwargs["safe_context"])
        self.reason_codes.append(cast(str, safe_context["reason_code"]))
        return DiagnosticRecord(
            id=uuid4(),
            correlation_id=cast(UUID, kwargs["correlation_id"]),
            severity=cast(str, kwargs["severity"]),
            safe_summary=cast(str, kwargs["safe_summary"]),
        )

    async def enqueue_admin_notifications(
        self, diagnostic_id: object, *, at: datetime
    ) -> int:
        del diagnostic_id, at
        return 0


@dataclass
class RecordingVersions:
    versions: dict[UUID, FlowVersionRecord]

    async def get(self, flow_version_id: UUID) -> FlowVersionRecord:
        try:
            return self.versions[flow_version_id]
        except KeyError as error:
            raise LookupError("flow version was not found") from error


@dataclass
class RecordingPendingIntents:
    records: list[PendingFlowIntentRecord] = field(default_factory=list)

    async def list_active(
        self, user_id: UUID, *, now: datetime
    ) -> list[PendingFlowIntentRecord]:
        return [
            record
            for record in self.records
            if record.user_id == user_id and record.expires_at > now
        ]

    async def append(
        self, intent: NewPendingFlowIntent, *, max_per_user: int
    ) -> PendingFlowIntentRecord:
        self.records = [
            record
            for record in self.records
            if not (
                record.user_id == intent.user_id
                and record.flow_version_id == intent.flow_version_id
                and record.flow_key == intent.flow_key
            )
        ]
        same_user = [
            record for record in self.records if record.user_id == intent.user_id
        ]
        other_users = [
            record for record in self.records if record.user_id != intent.user_id
        ]
        self.records = other_users + same_user[-max_per_user + 1 :]
        record = PendingFlowIntentRecord(
            id=uuid4(),
            position=len(same_user),
            user_id=intent.user_id,
            flow_key=intent.flow_key,
            flow_version_id=intent.flow_version_id,
            service_id=intent.service_id,
            created_at=intent.created_at,
            expires_at=intent.expires_at,
        )
        self.records.append(record)
        return record

    async def delete(self, intent_id: UUID) -> None:
        self.records = [record for record in self.records if record.id != intent_id]

    async def delete_expired(self, user_id: UUID, *, now: datetime) -> int:
        before = len(self.records)
        self.records = [
            record
            for record in self.records
            if record.user_id != user_id or record.expires_at > now
        ]
        return before - len(self.records)


@dataclass
class FakeUnitOfWork:
    user: UserRecord
    version: FlowVersionRecord
    branches: list[OpenSelectionState]
    services_by_id: dict[UUID, ServiceRecord]
    locked_user_ids: list[UUID] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.users = RecordingUsers(self.user)
        self.services = RecordingServices(self.services_by_id)
        self.attendances = RecordingAttendances()
        self.conversations = RecordingConversations()
        self.matches = RejectingMatches()
        self.diagnostics = RecordingDiagnostics()
        self.flow_versions = RecordingVersions({self.version.id: self.version})
        self.open_selections = RecordingSelections(self.branches)
        self.pending_intents = RecordingPendingIntents()

    async def lock_user(self, user_id: UUID) -> None:
        self.locked_user_ids.append(user_id)


def _root() -> DiscussionFlow:
    return DiscussionFlow.model_validate(
        {
            "key": "system.dispatch.root",
            "next_flow_mode": "checkpoint",
            "return_actions": [
                {"type": "send_message", "text": "Back at the checkpoint"}
            ],
            "next_flows": [
                {
                    "key": "system.dispatch.start",
                    "trigger": {"type": "button", "button_id": "dispatch.start"},
                    "actions": [{"type": "select_service_attendance"}],
                    "next_flow_mode": "one_and_once_only",
                    "next_flows": [
                        {
                            "key": "system.dispatch.event",
                            "trigger": {
                                "type": "action_event",
                                "event_key": "dispatch.found",
                            },
                            "actions": [
                                {"type": "send_message", "text": "Found a match"}
                            ],
                            "next_flow_mode": "one_and_once_only",
                            "next_flows": [
                                {
                                    "key": "system.dispatch.confirm",
                                    "trigger": {
                                        "type": "button",
                                        "button_id": "dispatch.confirm",
                                    },
                                    "actions": [
                                        {
                                            "type": "send_message",
                                            "text": "Connection confirmed",
                                        }
                                    ],
                                    "next_flow_mode": "one_and_once_only",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )


def _version(root: DiscussionFlow) -> FlowVersionRecord:
    return FlowVersionRecord(
        id=uuid4(),
        scope_kind=FlowScopeKind.SYSTEM,
        service_id=None,
        root_flow_key=str(root.key),
        definition=cast(dict[str, JsonValue], root.model_dump(mode="json")),
        content_hash="dispatch-test",
        published_at=NOW,
        published_by_user_id=None,
    )


def _application(
    user: UserRecord,
    version: FlowVersionRecord,
    service: ServiceRecord,
    *,
    router: ConstrainedRouter | None = None,
    onboarding: OnboardingService | None = None,
    services: ServiceAttendanceService | None = None,
) -> FriendlyBotApplication:
    registry = ActionExecutorRegistry()

    async def emit_found(
        action: SelectServiceAttendanceAction, context: ActionContext
    ) -> None:
        del action
        context.emit(ActionEvent(key="dispatch.found"))

    async def send(action: SendMessageAction, context: ActionContext) -> None:
        await context.queue_text_presentation(
            context.render(await context.message_template(action))
        )

    async def send_fixed(
        action: SendMessageFixedAction, context: ActionContext
    ) -> None:
        await context.queue_text_presentation(context.render(action.text))

    registry.register(SelectServiceAttendanceAction, emit_found)
    registry.register(SendMessageAction, send)
    registry.register(SendMessageFixedAction, send_fixed)
    zone_x = PublishedZoneX(
        service=service,
        map_url="https://maps.example.test/zone-x",
        system_root=version,
        service_root=version,
        latecomer_root=version,
        timestamps=(),
    )
    return FriendlyBotApplication(
        dependencies=ActionDependencies(
            telegram=cast(TelegramGateway, object()),
            services=services or cast(ServiceAttendanceService, object()),
            lifecycle=cast(ServiceLifecycleService, object()),
            matching=cast(MatchingService, object()),
            diagnostics=cast(DiagnosticRepository, object()),
        ),
        registry=registry,
        router=router or cast(ConstrainedRouter, AuthoredKnownFlowRouter()),
        zone_x=zone_x,
        onboarding=onboarding,
    )


def _message(
    user: UserRecord,
    *,
    message_id: int,
    text: str | None = None,
    callback: TelegramCallback | None = None,
) -> TelegramMessage:
    assert user.telegram_user_id is not None
    return TelegramMessage(
        message_id=message_id,
        sent_at=NOW,
        chat=TelegramChat(user.telegram_user_id, "private"),
        sender=TelegramUser(user.telegram_user_id),
        text=text,
        reply_text=None,
        callback=callback,
    )


def _fixture(
    *,
    display_name: str | None = "Ryan",
    router: ConstrainedRouter | None = None,
    onboarding: OnboardingService | None = None,
) -> tuple[FriendlyBotApplication, FakeUnitOfWork, UserRecord]:
    root = _root()
    version = _version(root)
    user = UserRecord(
        id=uuid4(),
        telegram_user_id=77,
        display_name=display_name,
        role=OperationalRole.NBNC,
        is_admin=False,
    )
    service = ServiceRecord(
        id=uuid4(),
        key="zone_x_2026_10_18",
        highkey=True,
        doors_open_at=NOW - timedelta(hours=1),
        doors_close_at=NOW + timedelta(hours=1),
        interaction_ends_at=NOW + timedelta(hours=2),
        name="Zone X",
    )
    branch = OpenSelectionState(
        id=uuid4(),
        user_id=user.id,
        flow_version_id=version.id,
        parent_flow_key=str(root.key),
        service_id=None,
        is_current=True,
        is_global_interruptive=True,
        ancestor_flow_keys=(str(root.key),),
        checkpoint_flow_keys=(str(root.key),),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    uow = FakeUnitOfWork(user, version, [branch], {service.id: service})
    return (
        _application(
            user,
            version,
            service,
            router=router,
            onboarding=onboarding,
            services=ServiceAttendanceService(lambda: cast(UnitOfWork, uow)),
        ),
        uow,
        user,
    )


@dataclass
class FailingRouter:
    error: GatewayTransportError | GatewayProtocolError

    async def route_update_in_uow(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise self.error


class RejectingRouter:
    async def route_update_in_uow(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("deterministic onboarding must not invoke model routing")


class AuthoredKnownFlowRouter:
    async def route_update_in_uow(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("deterministic flow must not invoke typed routing")

    async def plan_known_flow(self, request: KnownFlowRequest) -> PlannedFlowMatch:
        return PlannedFlowMatch(
            flow_id=request.flow_id,
            replies=tuple(
                PlannedReply(slot_id=slot.slot_id, text=slot.template)
                for slot in request.reply_slots
            ),
        )


@dataclass
class ResultRouter:
    result: MultiIntentRoutingResult
    typed_calls: int = 0
    known_calls: int = 0

    async def route_update_in_uow(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.typed_calls += 1
        return self.result

    async def plan_known_flow(self, request: KnownFlowRequest) -> PlannedFlowMatch:
        self.known_calls += 1
        return PlannedFlowMatch(
            flow_id=request.flow_id,
            replies=tuple(
                PlannedReply(slot_id=slot.slot_id, text=slot.template)
                for slot in request.reply_slots
            ),
        )


def _routing_candidate(
    flow: DiscussionFlow, version: FlowVersionRecord, *, interruptive: bool = False
) -> RoutingCandidate:
    return RoutingCandidate(
        key=str(flow.key),
        gists=("test request",),
        source="current",
        is_current=True,
        service_id=None,
        is_global_interruptive=interruptive,
        multi_intent_mode=flow.multi_intent_mode.value,
        response_plan=CandidateResponsePlan((), ()),
        flow_version_id=version.id,
    )


def _multi_fixture(
    root: DiscussionFlow, router: ResultRouter
) -> tuple[FriendlyBotApplication, FakeUnitOfWork, UserRecord]:
    version = _version(root)
    user = UserRecord(
        id=uuid4(),
        telegram_user_id=77,
        display_name="Ryan",
        role=OperationalRole.NBNC,
        is_admin=False,
    )
    service = ServiceRecord(
        id=uuid4(),
        key="zone_x_2026_10_18",
        highkey=True,
        doors_open_at=NOW - timedelta(hours=1),
        doors_close_at=NOW + timedelta(hours=1),
        interaction_ends_at=NOW + timedelta(hours=2),
        name="Zone X",
    )
    branch = OpenSelectionState(
        id=uuid4(),
        user_id=user.id,
        flow_version_id=version.id,
        parent_flow_key=str(root.key),
        service_id=None,
        is_current=True,
        is_global_interruptive=True,
        ancestor_flow_keys=(str(root.key),),
        checkpoint_flow_keys=(str(root.key),),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    uow = FakeUnitOfWork(user, version, [branch], {service.id: service})
    return (
        _application(user, version, service, router=cast(ConstrainedRouter, router)),
        uow,
        user,
    )


async def test_start_and_name_capture_do_not_invoke_model_routing() -> None:
    """Removing the onboarding boundary would send `/start` and the name to OpenRouter."""

    uow_holder: list[FakeUnitOfWork] = []
    onboarding = OnboardingService(lambda: cast(UnitOfWork, uow_holder[0]))
    application, uow, user = _fixture(
        display_name=None,
        router=cast(ConstrainedRouter, RejectingRouter()),
        onboarding=onboarding,
    )
    uow_holder.append(uow)

    started = await application.dispatch(
        user_id=user.id,
        incoming=_message(user, message_id=5, text="/start"),
        unit_of_work=cast(UnitOfWork, uow),
    )
    uow.conversations.messages.extend([object(), object()])
    named = await application.dispatch(
        user_id=user.id,
        incoming=_message(user, message_id=6, text="Ari"),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert started == DispatchResult(
        "onboarding",
        presentations=(
            TelegramTextPresentation(
                77,
                "Hey! Welcome to The Zone! Glad to see you here today!\n\n"
                "How may I address you?",
            ),
        ),
    )
    assert named == DispatchResult("onboarding")
    assert uow.users.user.display_name == "Ari"
    assert uow.attendances.started == [
        (uow.users.user.id, next(iter(uow.services_by_id)), "ordinary")
    ]


async def test_existing_start_opens_the_system_path_without_model_routing() -> None:
    """An existing user's `/start` must not depend on provider availability."""

    uow_holder: list[FakeUnitOfWork] = []
    onboarding = OnboardingService(lambda: cast(UnitOfWork, uow_holder[0]))
    application, uow, user = _fixture(
        router=cast(ConstrainedRouter, RejectingRouter()), onboarding=onboarding
    )
    uow_holder.append(uow)

    result = await application.dispatch(
        user_id=user.id,
        incoming=_message(user, message_id=7, text="/start"),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert result == DispatchResult("onboarding")
    assert [branch.parent_flow_key for branch in uow.open_selections.branches] == [
        "system.dispatch.root"
    ]


@pytest.mark.parametrize(
    ("error", "reason_code"),
    [
        (GatewayTransportError("provider unavailable"), "routing.provider_unavailable"),
        (
            GatewayProtocolError("provider returned invalid key"),
            "routing.provider_invalid_response",
        ),
    ],
)
async def test_routing_failure_commits_a_redacted_fallback(
    error: GatewayTransportError | GatewayProtocolError,
    reason_code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An exhausted provider must not terminate the Telegram runtime task group."""

    root = _root()
    version = _version(root)
    user = UserRecord(
        id=uuid4(),
        telegram_user_id=77,
        display_name="Ryan",
        role=OperationalRole.NBNC,
        is_admin=False,
    )
    service = ServiceRecord(
        id=uuid4(),
        key="zone_x_2026_10_18",
        highkey=True,
        doors_open_at=NOW - timedelta(hours=1),
        doors_close_at=NOW + timedelta(hours=1),
        interaction_ends_at=NOW + timedelta(hours=2),
        name="Zone X",
    )
    branch = OpenSelectionState(
        id=uuid4(),
        user_id=user.id,
        flow_version_id=version.id,
        parent_flow_key=str(root.key),
        service_id=None,
        is_current=True,
        is_global_interruptive=True,
        ancestor_flow_keys=(str(root.key),),
        checkpoint_flow_keys=(str(root.key),),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    uow = FakeUnitOfWork(user, version, [branch], {service.id: service})
    application = _application(
        user,
        version,
        service,
        router=cast(ConstrainedRouter, FailingRouter(error)),
    )

    result = await application.dispatch(
        user_id=user.id,
        incoming=_message(user, message_id=8, text="help"),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert result == DispatchResult(
        "failed",
        presentations=(
            TelegramTextPresentation(77, "Sorry, an error occurred. Error log: 77."),
        ),
    )
    assert uow.diagnostics.reason_codes == [reason_code]
    assert caplog.messages == [f"routing provider failure: {reason_code}"]
    assert str(error) not in caplog.text


async def test_dispatch_persists_event_child_for_its_later_button_callback() -> None:
    app, uow, user = _fixture()

    first = await app.dispatch(
        user_id=user.id,
        incoming=_message(
            user, callback=TelegramCallback("dispatch.start"), message_id=1
        ),
        unit_of_work=cast(UnitOfWork, uow),
    )
    second = await app.dispatch(
        user_id=user.id,
        incoming=_message(
            user, callback=TelegramCallback("dispatch.confirm"), message_id=2
        ),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert first.executed_flow_keys == (
        "system.dispatch.start",
        "system.dispatch.event",
    )
    assert first.presentations == (TelegramTextPresentation(77, "Found a match"),)
    assert second.executed_flow_keys == ("system.dispatch.confirm",)
    assert second.presentations == (
        TelegramTextPresentation(77, "Connection confirmed"),
        TelegramTextPresentation(77, "Back at the checkpoint"),
    )
    assert [branch.parent_flow_key for branch in uow.open_selections.branches] == [
        "system.dispatch.root"
    ]
    assert uow.open_selections.branches[0].is_current


async def test_expired_service_callback_sends_exact_fixed_copy_without_execution() -> (
    None
):
    app, uow, user = _fixture()
    service = next(iter(uow.services_by_id.values()))
    uow.services_by_id[service.id] = replace(
        service, interaction_ends_at=NOW - timedelta(seconds=1)
    )

    result = await app.dispatch(
        user_id=user.id,
        incoming=_message(
            user,
            callback=TelegramCallback(
                "dispatch.start",
                TelegramCallbackContextKind.SERVICE,
                service.id,
            ),
            message_id=3,
        ),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert result == DispatchResult(
        "expired",
        presentations=(TelegramTextPresentation(77, "Sorry, the service is over!"),),
    )


async def test_unknown_match_callback_is_ignored_without_branch_or_output_work() -> (
    None
):
    app, uow, user = _fixture()

    result = await app.dispatch(
        user_id=user.id,
        incoming=_message(
            user,
            callback=TelegramCallback(
                "dispatch.confirm",
                TelegramCallbackContextKind.MATCH_REQUEST,
                uuid4(),
            ),
            message_id=4,
        ),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert result == DispatchResult("ignored")


async def test_timestamp_preparer_opens_the_root_and_counts_its_own_outbox_work() -> (
    None
):
    app, uow, user = _fixture()
    service = next(iter(uow.services_by_id.values()))
    root = DiscussionFlow.model_validate(
        {
            "key": "service.zone_x.timestamp.notice",
            "next_flow_mode": "allow_many",
            "actions": [{"type": "send_message", "text": "Timestamp notice"}],
        }
    )
    version = FlowVersionRecord(
        id=uuid4(),
        scope_kind=FlowScopeKind.TIMESTAMP,
        service_id=service.id,
        root_flow_key=str(root.key),
        definition=cast(dict[str, JsonValue], root.model_dump(mode="json")),
        content_hash="timestamp-test",
        published_at=NOW,
        published_by_user_id=None,
    )
    uow.flow_versions.versions[version.id] = version
    timestamp = ServiceTimestampRecord(
        id=uuid4(),
        service_id=service.id,
        key="zone_x.test_notice",
        occurs_at=NOW,
        audience=ServiceAudience.ALL_NBNCS,
        flow_version_id=version.id,
        root_flow_key=str(root.key),
    )

    prepared = await app.open_for_recipient(
        cast(UnitOfWork, uow), timestamp, user.id, now=NOW
    )

    assert prepared.enqueued_delivery_count == 1
    assert [branch.parent_flow_key for branch in uow.open_selections.branches] == [
        "system.dispatch.root",
        "service.zone_x.timestamp.notice",
    ]


async def test_typed_dispatch_runs_all_answer_fragments_without_transition() -> None:
    root = DiscussionFlow.model_validate(
        {
            "key": "system.multi.root",
            "next_flow_mode": "checkpoint",
            "next_flows": [
                {
                    "key": "system.multi.answer_one",
                    "trigger": {"type": "message", "llm_gist": "first answer"},
                    "multi_intent_mode": "answer",
                    "actions": [{"type": "send_message", "text": "first"}],
                    "next_flow_mode": "one_and_once_only",
                },
                {
                    "key": "system.multi.answer_two",
                    "trigger": {"type": "message", "llm_gist": "second answer"},
                    "multi_intent_mode": "answer",
                    "actions": [{"type": "send_message", "text": "second"}],
                    "next_flow_mode": "one_and_once_only",
                },
            ],
        }
    )
    version = _version(root)
    first, second = root.next_flows
    router = ResultRouter(
        MultiIntentRoutingResult(
            answers=(
                RoutedMatch(
                    _routing_candidate(first, version),
                    ReplyPlan((PlannedActionText(str(first.key), 0, "first reply"),)),
                ),
                RoutedMatch(
                    _routing_candidate(second, version),
                    ReplyPlan((PlannedActionText(str(second.key), 0, "second reply"),)),
                ),
            ),
            interactive=None,
            deferred=(),
            terminal=None,
        )
    )
    app, uow, user = _multi_fixture(root, router)
    uow.flow_versions.versions[version.id] = version
    uow.open_selections.branches[0] = replace(
        uow.open_selections.branches[0],
        flow_version_id=version.id,
        parent_flow_key=str(root.key),
        ancestor_flow_keys=(str(root.key),),
        checkpoint_flow_keys=(str(root.key),),
    )

    result = await app.dispatch(
        user_id=user.id,
        incoming=_message(user, message_id=21, text="both please"),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert result.executed_flow_keys == (str(first.key), str(second.key))
    assert result.presentations == (
        TelegramTextPresentation(77, "first reply"),
        TelegramTextPresentation(77, "second reply"),
    )
    assert [branch.parent_flow_key for branch in uow.open_selections.branches] == [
        str(root.key)
    ]
    assert router.typed_calls == 1


async def test_typed_dispatch_defers_extra_interactive_matches_in_order() -> None:
    root = DiscussionFlow.model_validate(
        {
            "key": "system.pending.root",
            "next_flow_mode": "checkpoint",
            "next_flows": [
                {
                    "key": "system.pending.first",
                    "trigger": {"type": "message", "llm_gist": "first"},
                    "actions": [{"type": "send_message", "text": "first"}],
                    "next_flow_mode": "one_and_once_only",
                    "next_flows": [
                        {
                            "key": "system.pending.first.wait",
                            "trigger": {"type": "button", "button_id": "wait"},
                            "next_flow_mode": "one_and_once_only",
                        }
                    ],
                },
                {
                    "key": "system.pending.second",
                    "trigger": {"type": "message", "llm_gist": "second"},
                    "actions": [{"type": "send_message", "text": "second"}],
                    "next_flow_mode": "one_and_once_only",
                },
                {
                    "key": "system.pending.third",
                    "trigger": {"type": "message", "llm_gist": "third"},
                    "actions": [{"type": "send_message", "text": "third"}],
                    "next_flow_mode": "one_and_once_only",
                },
            ],
        }
    )
    version = _version(root)
    first, second, third = root.next_flows
    first_match = RoutedMatch(
        _routing_candidate(first, version),
        ReplyPlan((PlannedActionText(str(first.key), 0, "first reply"),)),
    )
    router = ResultRouter(
        MultiIntentRoutingResult(
            answers=(),
            interactive=first_match,
            deferred=(
                _routing_candidate(second, version),
                _routing_candidate(third, version),
            ),
            terminal=None,
        )
    )
    app, uow, user = _multi_fixture(root, router)
    uow.flow_versions.versions[version.id] = version
    uow.open_selections.branches[0] = replace(
        uow.open_selections.branches[0],
        flow_version_id=version.id,
        parent_flow_key=str(root.key),
        ancestor_flow_keys=(str(root.key),),
        checkpoint_flow_keys=(str(root.key),),
    )

    result = await app.dispatch(
        user_id=user.id,
        incoming=_message(user, message_id=22, text="all three"),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert result.executed_flow_keys == (str(first.key),)
    assert result.presentations == (TelegramTextPresentation(77, "first reply"),)
    assert [intent.flow_key for intent in uow.pending_intents.records] == [
        str(second.key),
        str(third.key),
    ]


async def test_completed_interactive_flow_resumes_one_pending_intent() -> None:
    root = DiscussionFlow.model_validate(
        {
            "key": "system.resume.root",
            "next_flow_mode": "checkpoint",
            "next_flows": [
                {
                    "key": "system.resume.first",
                    "trigger": {"type": "message", "llm_gist": "first"},
                    "actions": [{"type": "send_message", "text": "first"}],
                    "next_flow_mode": "one_and_once_only",
                },
                {
                    "key": "system.resume.second",
                    "trigger": {"type": "message", "llm_gist": "second"},
                    "actions": [{"type": "send_message", "text": "second"}],
                    "next_flow_mode": "one_and_once_only",
                },
                {
                    "key": "system.resume.third",
                    "trigger": {"type": "message", "llm_gist": "third"},
                    "actions": [{"type": "send_message", "text": "third"}],
                    "next_flow_mode": "one_and_once_only",
                },
            ],
        }
    )
    version = _version(root)
    first, second, third = root.next_flows
    router = ResultRouter(
        MultiIntentRoutingResult(
            answers=(),
            interactive=RoutedMatch(
                _routing_candidate(first, version),
                ReplyPlan((PlannedActionText(str(first.key), 0, "first reply"),)),
            ),
            deferred=(
                _routing_candidate(second, version),
                _routing_candidate(third, version),
            ),
            terminal=None,
        )
    )
    app, uow, user = _multi_fixture(root, router)
    uow.flow_versions.versions[version.id] = version
    uow.open_selections.branches[0] = replace(
        uow.open_selections.branches[0],
        flow_version_id=version.id,
        parent_flow_key=str(root.key),
        ancestor_flow_keys=(str(root.key),),
        checkpoint_flow_keys=(str(root.key),),
    )

    result = await app.dispatch(
        user_id=user.id,
        incoming=_message(user, message_id=23, text="all three"),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert result.executed_flow_keys == (str(first.key), str(second.key))
    assert result.presentations == (
        TelegramTextPresentation(77, "first reply"),
        TelegramTextPresentation(77, "second"),
    )
    assert [intent.flow_key for intent in uow.pending_intents.records] == [
        str(third.key)
    ]


async def test_interruptive_safety_result_excludes_other_matches() -> None:
    root = DiscussionFlow.model_validate(
        {
            "key": "system.safety.root",
            "next_flow_mode": "checkpoint",
            "next_flows": [
                {
                    "key": "system.global.safety",
                    "trigger": {"type": "message", "llm_gist": "unsafe"},
                    "actions": [{"type": "send_message", "text": "safety"}],
                    "next_flow_mode": "one_and_once_only",
                },
                {
                    "key": "system.safety.answer",
                    "trigger": {"type": "message", "llm_gist": "answer"},
                    "multi_intent_mode": "answer",
                    "actions": [{"type": "send_message", "text": "answer"}],
                    "next_flow_mode": "one_and_once_only",
                },
            ],
        }
    )
    version = _version(root)
    safety = root.next_flows[0]
    router = ResultRouter(
        MultiIntentRoutingResult(
            answers=(),
            interactive=RoutedMatch(
                _routing_candidate(safety, version, interruptive=True),
                ReplyPlan((PlannedActionText(str(safety.key), 0, "safety reply"),)),
            ),
            deferred=(),
            terminal=None,
        )
    )
    app, uow, user = _multi_fixture(root, router)
    uow.flow_versions.versions[version.id] = version
    uow.open_selections.branches[0] = replace(
        uow.open_selections.branches[0],
        flow_version_id=version.id,
        parent_flow_key=str(root.key),
        ancestor_flow_keys=(str(root.key),),
        checkpoint_flow_keys=(str(root.key),),
    )

    result = await app.dispatch(
        user_id=user.id,
        incoming=_message(user, message_id=24, text="unsafe and answer"),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert result.presentations == (TelegramTextPresentation(77, "safety reply"),)
    assert uow.pending_intents.records == []


async def test_button_flow_with_ordinary_copy_plans_once() -> None:
    router = ResultRouter(MultiIntentRoutingResult((), None, (), None))
    app, uow, user = _fixture(router=cast(ConstrainedRouter, router))

    result = await app.dispatch(
        user_id=user.id,
        incoming=_message(
            user, callback=TelegramCallback("dispatch.start"), message_id=25
        ),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert result.presentations == (TelegramTextPresentation(77, "Found a match"),)
    assert router.typed_calls == 0
    assert router.known_calls == 1


async def test_fixed_only_button_flow_skips_known_flow_planning() -> None:
    root = DiscussionFlow.model_validate(
        {
            "key": "system.fixed.root",
            "next_flow_mode": "checkpoint",
            "next_flows": [
                {
                    "key": "system.fixed.reply",
                    "trigger": {"type": "button", "button_id": "fixed.reply"},
                    "actions": [
                        {"type": "send_message_fixed", "text": "Exact fixed copy"}
                    ],
                    "next_flow_mode": "one_and_once_only",
                }
            ],
        }
    )
    router = ResultRouter(MultiIntentRoutingResult((), None, (), None))
    app, uow, user = _multi_fixture(root, router)

    result = await app.dispatch(
        user_id=user.id,
        incoming=_message(
            user, callback=TelegramCallback("fixed.reply"), message_id=26
        ),
        unit_of_work=cast(UnitOfWork, uow),
    )

    assert result.presentations == (TelegramTextPresentation(77, "Exact fixed copy"),)
    assert router.typed_calls == 0
    assert router.known_calls == 0
