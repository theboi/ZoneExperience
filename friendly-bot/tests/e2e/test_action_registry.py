"""I04's closed action-dispatch boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from friendly_bot.actions.context import (
    ActionContext,
    MissingPlannedLlmReplyError,
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
    SendMessageFixedAction,
    SendMessageLlmAction,
    SendMessageParaphrasedAction,
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
    DiagnosticRecord,
    DiagnosticRepository,
    FlowVersionRecord,
    ServiceRecord,
    UserRecord,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.responses.planner import PlannedActionText, ReplyPlan
from friendly_bot.services import ServiceAttendanceService, ServiceLifecycleService
from friendly_bot.telegram import (
    TelegramActivityConfirmed,
    TelegramCallback,
    TelegramCallbackContextKind,
    TelegramGateway,
    TelegramInlineButton,
    TelegramPhotoPresentation,
    TelegramTextPresentation,
    decode_callback,
)


class RecordingUnitOfWork:
    pass


@dataclass
class RecordingDiagnostics:
    reason_codes: list[str] = field(default_factory=list)

    async def record(self, **kwargs: object) -> DiagnosticRecord:
        safe_context = cast(dict[str, str], kwargs["safe_context"])
        self.reason_codes.append(safe_context["reason_code"])
        return DiagnosticRecord(
            id=uuid4(),
            correlation_id=cast(UUID, kwargs["correlation_id"]),
            severity=cast(str, kwargs["severity"]),
            safe_summary=cast(str, kwargs["safe_summary"]),
        )


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
    assert len(action_types) == 26


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
    registry.register(SendMessageParaphrasedAction, _do_nothing)

    with pytest.raises(DuplicateActionExecutorError):
        registry.register(SendMessageParaphrasedAction, _do_nothing)
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

    context, _ = _context(now)
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

    assert context.presentation_buffer.snapshot() == (
        TelegramTextPresentation(42, "Call a trusted adult now, Ryan."),
    )


async def test_ordinary_message_uses_its_planned_reply_and_fixed_copy_does_not(
    now: datetime,
) -> None:
    context, _ = _context(now)
    context.reply_plan = ReplyPlan(
        (PlannedActionText("system.greeting", 0, "hey Ryan"),)
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
    ordinary = SendMessageParaphrasedAction(
        type="send_message_paraphrased", text="Hello {{ user.display_name }}"
    )
    fixed = SendMessageFixedAction(type="send_message_fixed", text="Call 999 now")

    await registry.resolve(ordinary)(ordinary, context.for_action("system.greeting", 0))
    await registry.resolve(fixed)(fixed, context.for_action("system.greeting", 1))
    await context.flush_presentation()

    assert context.presentation_buffer.snapshot() == (
        TelegramTextPresentation(42, "hey Ryan"),
        TelegramTextPresentation(42, "Call 999 now"),
    )


async def test_source_grounded_message_uses_only_its_planned_reply(
    now: datetime,
) -> None:
    context, _ = _context(now)
    source = "arrow is for post-secondary students and nsfs aged 17-23."
    action = SendMessageLlmAction(type="send_message_llm", source=source)
    registry = build_action_registry(
        ActionDependencies(
            telegram=context.telegram,
            services=cast(ServiceAttendanceService, object()),
            lifecycle=cast(ServiceLifecycleService, object()),
            matching=cast(MatchingService, object()),
            diagnostics=cast(DiagnosticRepository, object()),
        )
    )

    context.reply_plan = ReplyPlan(
        (PlannedActionText("system.information", 0, "yes, arrow is for you."),)
    )
    await registry.resolve(action)(action, context.for_action("system.information", 0))
    await context.flush_presentation()
    assert context.presentation_buffer.snapshot() == (
        TelegramTextPresentation(42, "yes, arrow is for you."),
    )

    fallback_context, _ = _context(now)
    fallback_context.reply_plan = ReplyPlan((), frozenset({("system.information", 0)}))
    with pytest.raises(MissingPlannedLlmReplyError):
        await registry.resolve(action)(
            action, fallback_context.for_action("system.information", 0)
        )
    assert fallback_context.presentation_buffer.snapshot() == ()


async def test_missing_planned_reply_uses_authored_copy_and_records_fallback(
    now: datetime,
) -> None:
    context, _ = _context(now)
    diagnostics = RecordingDiagnostics()
    context.diagnostics = cast(DiagnosticRepository, diagnostics)
    context.reply_plan = ReplyPlan((), frozenset({("system.greeting", 0)}))
    action = SendMessageParaphrasedAction(
        type="send_message_paraphrased", text="Hello {{ user.display_name }}"
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

    await registry.resolve(action)(action, context.for_action("system.greeting", 0))
    await context.flush_presentation()

    assert context.presentation_buffer.snapshot() == (
        TelegramTextPresentation(42, "Hello Ryan"),
    )
    assert diagnostics.reason_codes == ["llm_reply.validation_fallback"]


async def test_context_renders_locally_queues_delivery_and_allows_one_event(
    now: datetime,
) -> None:
    context, _ = _context(now)

    assert context.render("Hi {{user.display_name}} at {{service.id}}") == (
        "Hi Ryan at 30375598-1898-4d46-a1d8-2068453944e4"
    )
    assert context.render_uuid("{{service.id}}") == UUID(
        "30375598-1898-4d46-a1d8-2068453944e4"
    )
    await context.enqueue_text("local fixed copy")

    assert context.presentation_buffer.snapshot() == (
        TelegramTextPresentation(42, "local fixed copy"),
    )

    event = ActionEvent(key="human_match.found", payload={"request_id": "local"})
    context.emit(event)
    assert context.terminal_event == event
    with pytest.raises(TerminalActionEventAlreadyEmittedError):
        context.emit(ActionEvent(key="human_match.not_found"))


async def test_context_composes_text_or_photo_with_inline_buttons(
    now: datetime,
) -> None:
    context, _ = _context(now)

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

    assert context.presentation_buffer.snapshot() == (
        TelegramTextPresentation(
            42,
            "Choose an option",
            (TelegramInlineButton("Continue", "zone_x.menu.connect"),),
        ),
        TelegramPhotoPresentation(
            42,
            "zone_x_poster_2026",
            "Zone X",
            (TelegramInlineButton("Directions", "zone_x.menu.directions"),),
        ),
    )


async def test_context_rejects_orphan_textless_buttons(now: datetime) -> None:
    context, _ = _context(now)

    with pytest.raises(OrphanPresentationButtonsError):
        await context.attach_presentation_buttons(
            (TelegramInlineButton("Continue", "zone_x.menu.connect"),)
        )


async def test_child_context_preserves_parent_presentation_order(
    now: datetime,
) -> None:
    context, _ = _context(now)

    await context.enqueue_text("parent")
    child = context.for_child(context.flow, event_payload=None)
    await child.enqueue_text("child")

    assert context.presentation_buffer.snapshot() == (
        TelegramTextPresentation(42, "parent"),
        TelegramTextPresentation(42, "child"),
    )


async def test_parent_after_direct_child_keeps_shared_presentation_order(
    now: datetime,
) -> None:
    """Return actions after an event child retain their execution order."""

    context, _ = _context(now)
    child = context.for_child(context.flow, event_payload=None)

    await child.enqueue_text("event child")
    await context.enqueue_text("return action")

    assert context.presentation_buffer.snapshot() == (
        TelegramTextPresentation(42, "event child"),
        TelegramTextPresentation(42, "return action"),
    )


async def test_explicit_presentation_executors_use_typed_callbacks_and_activity(
    now: datetime,
) -> None:
    context, _ = _context(now)
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

    presentations = context.presentation_buffer.snapshot()
    assert [type(presentation) for presentation in presentations] == [
        TelegramTextPresentation,
        TelegramPhotoPresentation,
        TelegramTextPresentation,
    ]
    first = cast(TelegramTextPresentation, presentations[0])
    first_callback = first.buttons[0].callback_data
    assert decode_callback(first_callback) == TelegramCallback(
        button_id="zone_x.menu.connect",
        context_kind=TelegramCallbackContextKind.SERVICE,
        context_id=service_id,
    )
    assert presentations[1] == TelegramPhotoPresentation(
        42, "zone_x_poster_2026", "Poster"
    )
    choice = cast(TelegramTextPresentation, presentations[2])
    choice_callback = choice.buttons[0].callback_data
    assert decode_callback(choice_callback).context_id == service_id
    assert telegram.activities == [(42, "typing")]


def test_context_rejects_reserved_error_event(now: datetime) -> None:
    context, _ = _context(now)

    with pytest.raises(ReservedActionEventError):
        context.emit(ActionEvent(key="error"))
