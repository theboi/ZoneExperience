"""I04's closed action-dispatch boundary."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from friendly_bot.actions.context import (
    ActionContext,
    ReservedActionEventError,
    TerminalActionEventAlreadyEmittedError,
)
from friendly_bot.actions.registry import (
    ActionExecutorRegistry,
    ActionRegistryCompletenessError,
    DuplicateActionExecutorError,
    UnregisteredActionExecutorError,
    concrete_action_types,
)
from friendly_bot.domain.actions import (
    DiscussionAction,
    DiscussionActionBase,
    EndServiceInteractionsAction,
    SendMessageAction,
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
    UserRecord,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.services import ServiceAttendanceService, ServiceLifecycleService
from friendly_bot.telegram import TelegramGateway


class RecordingDeliveries:
    def __init__(self) -> None:
        self.enqueued: list[NewOutboundDelivery] = []

    async def enqueue(self, delivery: NewOutboundDelivery) -> object:
        self.enqueued.append(delivery)
        return object()


class RecordingUnitOfWork:
    def __init__(self) -> None:
        self.deliveries = RecordingDeliveries()


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
    assert len(action_types) == 24


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


def test_context_rejects_reserved_error_event(now: datetime) -> None:
    context, _ = _context(now)

    with pytest.raises(ReservedActionEventError):
        context.emit(ActionEvent(key="error"))
