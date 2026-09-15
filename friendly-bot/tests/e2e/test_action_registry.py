"""I04's closed action-dispatch boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from friendly_bot.actions.context import (
    ActionContext,
    OrphanPresentationButtonsError,
    ReservedActionEventError,
    TerminalActionEventAlreadyEmittedError,
)
from friendly_bot.actions.registry import (
    ActionDependencies,
    ActionExecutorRegistry,
    ActionRegistryCompletenessError,
    DuplicateActionExecutorError,
    UnregisteredActionExecutorError,
    build_action_registry,
    concrete_action_types,
)
from friendly_bot.domain.actions import (
    ButtonDefinition,
    DiscussionAction,
    DiscussionActionBase,
    EndServiceInteractionsAction,
    SendButtonsAction,
    SendMessageAction,
    SendMessageFixedAction,
    SendPhotoAction,
    SendServiceChoiceButtonsAction,
    ShowActivityAction,
)
from friendly_bot.domain.events import ActionEvent
from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode
from friendly_bot.domain.state import OpenSelectionState
from friendly_bot.matching.service import MatchingService
from friendly_bot.persistence.models import FlowScopeKind, OperationalRole
from friendly_bot.persistence.repositories import (
    DiagnosticRepository,
    FlowVersionRecord,
    NewOutboundDelivery,
    ServiceRecord,
    UserRecord,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.services import ServiceAttendanceService, ServiceLifecycleService
from friendly_bot.telegram import (
    TelegramActivityConfirmed,
    TelegramCallback,
    TelegramCallbackContextKind,
    TelegramGateway,
    TelegramInlineButton,
    decode_callback,
)


class RecordingDeliveries:
    def __init__(self) -> None:
        self.enqueued: list[NewOutboundDelivery] = []

    async def enqueue(self, delivery: NewOutboundDelivery) -> object:
        self.enqueued.append(delivery)
        return object()


class RecordingUnitOfWork:
    def __init__(self) -> None:
        self.deliveries = RecordingDeliveries()


@dataclass
class RecordingTelegram:
    activities: list[tuple[int, str]] = field(default_factory=list)

    async def send_activity(self, *, chat_id: int, activity: str) -> object:
        self.activities.append((chat_id, activity))
        return TelegramActivityConfirmed()


async def _do_nothing(action: DiscussionActionBase, context: ActionContext) -> None:
    del action, context


def _context(now: datetime) -> tuple[ActionContext, RecordingUnitOfWork]:
    user_id = uuid4()
    flow_version_id = uuid4()
    uow = RecordingUnitOfWork()
    context = ActionContext(
        user=UserRecord(
            id=user_id,
            telegram_user_id=42,
            display_name="Ryan",
            role=OperationalRole.NBNC,
            is_admin=False,
        ),
        incoming=None,
        flow=DiscussionFlow(
            key="system.test.root",
            next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
        ),
        flow_version=FlowVersionRecord(
            id=flow_version_id,
            scope_kind=FlowScopeKind.SYSTEM,
            service_id=None,
            root_flow_key="system.test.root",
            definition={},
            content_hash="test",
            published_at=now,
            published_by_user_id=None,
        ),
        branch=OpenSelectionState(
            id=uuid4(),
            user_id=user_id,
            flow_version_id=flow_version_id,
            parent_flow_key="system.test.root",
            service_id=None,
            is_current=True,
            is_global_interruptive=False,
            ancestor_flow_keys=("system.test.root",),
            checkpoint_flow_keys=(),
            opened_at=now,
            last_focused_at=now,
        ),
        unit_of_work=cast(UnitOfWork, uow),
        now=now,
        correlation_id=uuid4(),
        local_values={"service.id": "30375598-1898-4d46-a1d8-2068453944e4"},
        telegram=cast(TelegramGateway, object()),
        services=cast(ServiceAttendanceService, object()),
        lifecycle=cast(ServiceLifecycleService, object()),
        matching=cast(MatchingService, object()),
        diagnostics=cast(DiagnosticRepository, object()),
    )
    return context, uow


def test_registry_can_be_exactly_complete_for_f01_action_classes() -> None:
    action_types = concrete_action_types(DiscussionAction)
    registry = ActionExecutorRegistry()

    for action_type in action_types:
        registry.register(action_type, _do_nothing)

    registry.assert_complete(action_types)
    assert len(action_types) == 25


def test_composed_registry_registers_every_declared_action_exactly_once() -> None:
    dependencies = ActionDependencies(
        telegram=cast(TelegramGateway, object()),
        services=cast(ServiceAttendanceService, object()),
        lifecycle=cast(ServiceLifecycleService, object()),
        matching=cast(MatchingService, object()),
        diagnostics=cast(DiagnosticRepository, object()),
    )

    registry = build_action_registry(dependencies)

    registry.assert_complete(concrete_action_types(DiscussionAction))


def test_registry_rejects_duplicate_missing_and_extra_executors() -> None:
    registry = ActionExecutorRegistry()
    registry.register(SendMessageAction, _do_nothing)

    with pytest.raises(DuplicateActionExecutorError):
        registry.register(SendMessageAction, _do_nothing)
    with pytest.raises(UnregisteredActionExecutorError):
        registry.resolve(
            EndServiceInteractionsAction(
                type="end_service_interactions",
                expire_service_bound_selections=True,
                release_service_match_capacity=True,
                return_to_system_checkpoint=True,
            )
        )
    with pytest.raises(ActionRegistryCompletenessError):
        registry.assert_complete(concrete_action_types(DiscussionAction))


async def test_fixed_message_executor_renders_and_queues_exact_copy(
    now: datetime,
) -> None:
    """Breaks if fixed configured copy is sent through paraphrase behavior."""

    context, uow = _context(now)
    action = SendMessageFixedAction(
        type="send_message_fixed",
        text="Call a trusted adult now, {{ user.display_name }}.",
    )

    registry = build_action_registry(
        ActionDependencies(
            telegram=context.telegram,
            services=cast(ServiceAttendanceService, object()),
            lifecycle=cast(ServiceLifecycleService, object()),
            matching=cast(MatchingService, object()),
            diagnostics=cast(DiagnosticRepository, object()),
        )
    )
    await registry.resolve(action)(action, context)
    await context.flush_presentation()

    assert uow.deliveries.enqueued[0].payload == {
        "text": "Call a trusted adult now, Ryan.",
        "buttons": [],
    }


async def test_context_renders_locally_queues_delivery_and_allows_one_event(
    now: datetime,
) -> None:
    context, uow = _context(now)

    assert context.render("Hi {{user.display_name}} at {{service.id}}") == (
        "Hi Ryan at 30375598-1898-4d46-a1d8-2068453944e4"
    )
    assert context.render_uuid("{{service.id}}") == UUID(
        "30375598-1898-4d46-a1d8-2068453944e4"
    )
    await context.enqueue_text("local fixed copy")

    assert len(uow.deliveries.enqueued) == 1
    delivery = uow.deliveries.enqueued[0]
    assert delivery.user_id == context.user.id
    assert delivery.telegram_chat_id == 42
    assert delivery.payload == {"text": "local fixed copy"}
    assert str(context.correlation_id) in delivery.idempotency_key

    event = ActionEvent(key="human_match.found", payload={"request_id": "local"})
    context.emit(event)
    assert context.terminal_event == event
    with pytest.raises(TerminalActionEventAlreadyEmittedError):
        context.emit(ActionEvent(key="human_match.not_found"))


async def test_context_composes_text_or_photo_with_inline_buttons(
    now: datetime,
) -> None:
    context, uow = _context(now)

    await context.queue_text_presentation("Choose an option")
    await context.attach_presentation_buttons(
        (TelegramInlineButton("Continue", "zone_x.menu.connect"),)
    )
    await context.flush_presentation()
    await context.queue_photo_presentation(
        asset_key="zone_x_poster_2026", caption="Zone X"
    )
    await context.attach_presentation_buttons(
        (TelegramInlineButton("Directions", "zone_x.menu.directions"),)
    )
    await context.flush_presentation()

    assert [delivery.kind for delivery in uow.deliveries.enqueued] == [
        "message",
        "photo",
    ]
    assert uow.deliveries.enqueued[0].payload == {
        "text": "Choose an option",
        "buttons": [{"text": "Continue", "callback_data": "zone_x.menu.connect"}],
    }
    assert uow.deliveries.enqueued[1].payload == {
        "asset_key": "zone_x_poster_2026",
        "caption": "Zone X",
        "buttons": [{"text": "Directions", "callback_data": "zone_x.menu.directions"}],
    }


async def test_context_rejects_orphan_textless_buttons(now: datetime) -> None:
    context, _ = _context(now)

    with pytest.raises(OrphanPresentationButtonsError):
        await context.attach_presentation_buttons(
            (TelegramInlineButton("Continue", "zone_x.menu.connect"),)
        )


async def test_child_context_preserves_parent_delivery_idempotency_sequence(
    now: datetime,
) -> None:
    context, uow = _context(now)

    await context.enqueue_text("parent")
    child = context.for_child(context.flow, event_payload=None)
    await child.enqueue_text("child")

    assert [
        delivery.idempotency_key.rsplit(":", maxsplit=1)[-1]
        for delivery in uow.deliveries.enqueued
    ] == [
        "1",
        "2",
    ]


async def test_parent_after_direct_child_keeps_shared_delivery_sequence(
    now: datetime,
) -> None:
    """Return actions after an event child must not reuse an already queued suffix."""

    context, uow = _context(now)
    child = context.for_child(context.flow, event_payload=None)

    await child.enqueue_text("event child")
    await context.enqueue_text("return action")

    assert [
        delivery.idempotency_key.rsplit(":", maxsplit=1)[-1]
        for delivery in uow.deliveries.enqueued
    ] == ["1", "2"]


async def test_explicit_presentation_executors_use_typed_callbacks_and_activity(
    now: datetime,
) -> None:
    context, uow = _context(now)
    service_id = context.selected_service_id()
    registry = build_action_registry(
        ActionDependencies(
            telegram=context.telegram,
            services=cast(ServiceAttendanceService, object()),
            lifecycle=cast(ServiceLifecycleService, object()),
            matching=cast(MatchingService, object()),
            diagnostics=cast(DiagnosticRepository, object()),
        )
    )
    action = SendButtonsAction(
        type="send_buttons",
        service_bound=True,
        text="Welcome to Zone X",
        buttons=[
            ButtonDefinition(button_id="zone_x.menu.connect", text="Meet someone")
        ],
    )

    await registry.resolve(action)(action, context)
    await registry.resolve(
        SendPhotoAction(
            type="send_photo", asset_key="zone_x_poster_2026", caption="Poster"
        )
    )(
        SendPhotoAction(
            type="send_photo", asset_key="zone_x_poster_2026", caption="Poster"
        ),
        context,
    )
    context.set_service_choices(
        (
            ServiceRecord(
                id=service_id,
                key="zone_x_2026_10_18",
                highkey=True,
                doors_open_at=now - timedelta(hours=1),
                doors_close_at=now + timedelta(hours=1),
                interaction_ends_at=now + timedelta(hours=2),
                name="Zone X",
            ),
        )
    )
    choices = SendServiceChoiceButtonsAction(
        type="send_service_choice_buttons",
        button_id="service.attendance.select",
        text="Which service would you like to attend?",
    )
    await registry.resolve(choices)(choices, context)
    telegram = RecordingTelegram()
    context.telegram = cast(TelegramGateway, telegram)
    activity = ShowActivityAction(type="show_activity", activity="typing")
    await registry.resolve(activity)(activity, context)
    await context.flush_presentation()

    assert [delivery.kind for delivery in uow.deliveries.enqueued] == [
        "message",
        "photo",
        "message",
    ]
    first_buttons = cast(
        list[dict[str, str]], uow.deliveries.enqueued[0].payload["buttons"]
    )
    first_callback = first_buttons[0]["callback_data"]
    assert decode_callback(first_callback) == TelegramCallback(
        button_id="zone_x.menu.connect",
        context_kind=TelegramCallbackContextKind.SERVICE,
        context_id=service_id,
    )
    assert uow.deliveries.enqueued[1].payload == {
        "asset_key": "zone_x_poster_2026",
        "caption": "Poster",
        "buttons": [],
    }
    choice_buttons = cast(
        list[dict[str, str]], uow.deliveries.enqueued[2].payload["buttons"]
    )
    choice_callback = choice_buttons[0]["callback_data"]
    assert decode_callback(choice_callback).context_id == service_id
    assert telegram.activities == [(42, "typing")]


def test_context_rejects_reserved_error_event(now: datetime) -> None:
    context, _ = _context(now)

    with pytest.raises(ReservedActionEventError):
        context.emit(ActionEvent(key="error"))
