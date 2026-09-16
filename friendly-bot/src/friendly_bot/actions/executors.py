"""Explicit I04 executors for the closed F01 action union."""

from __future__ import annotations

import logging
from uuid import UUID

from friendly_bot.actions.context import ActionContext
from friendly_bot.domain.actions import (
    AddServiceAttendanceAction,
    ButtonDefinition,
    ConfirmHumanMatchAction,
    EndServiceInteractionsAction,
    EnterSelectedServiceCheckpointAction,
    EnterSelectedServiceLatecomerFlowAction,
    EnterServiceCheckpointAction,
    ExcludePreviousHumanFromNextAttemptAction,
    FindAndReserveSafetyResponderAction,
    FindAndReserveServerAction,
    MarkSafetyRequestPendingAction,
    NotifyAllAdminsAction,
    NotifyMatchedHumanAction,
    NotifyPreviousHumanAction,
    ReleaseHumanMatchAction,
    ResolveServiceSwitchOptionsAction,
    ReturnToNearestCheckpointAction,
    SaveIncomingAction,
    SelectServiceAttendanceAction,
    SendButtonsAction,
    SendMessageAction,
    SendMessageFixedAction,
    SendPhotoAction,
    SendServiceChoiceButtonsAction,
    ShareHumanContactAction,
    ShowActivityAction,
)
from friendly_bot.domain.events import ActionEvent
from friendly_bot.persistence.repositories import (
    MatchRequestRecord,
    MatchResponderRecord,
)
from friendly_bot.services.attendance import AttendanceOutcome
from friendly_bot.telegram import (
    TelegramActivityConfirmed,
    TelegramCallback,
    TelegramCallbackContextKind,
    TelegramInlineButton,
    encode_callback,
)

LOGGER = logging.getLogger(__name__)


async def send_message(action: SendMessageAction, context: ActionContext) -> None:
    """Buffer one configured requester-facing text presentation."""

    await context.queue_text_presentation(
        context.render(await context.message_template(action))
    )


async def send_message_fixed(
    action: SendMessageFixedAction, context: ActionContext
) -> None:
    """Buffer fixed requester-facing text with only local template rendering."""

    await context.queue_text_presentation(context.render(action.text))


async def send_buttons(action: SendButtonsAction, context: ActionContext) -> None:
    """Buffer configured inline buttons with only local callback context."""

    if action.text is not None:
        await context.queue_text_presentation(context.render(action.text))
    await context.attach_presentation_buttons(
        await _buttons_for(context, action.buttons, service_bound=action.service_bound)
    )


async def send_service_choice_buttons(
    action: SendServiceChoiceButtonsAction, context: ActionContext
) -> None:
    """Render F01-derived active service choices without configuration-side UUIDs."""

    if not context.service_choices:
        raise ValueError("service choice action has no current service options")
    await context.queue_text_presentation(context.render(action.text))
    buttons = tuple(
        TelegramInlineButton(
            text=service.name or service.key,
            callback_data=encode_callback(
                TelegramCallback(
                    button_id=action.button_id,
                    context_kind=TelegramCallbackContextKind.SERVICE,
                    context_id=service.id,
                )
            ),
        )
        for service in context.service_choices
    )
    await context.attach_presentation_buttons(buttons)


async def send_photo(action: SendPhotoAction, context: ActionContext) -> None:
    """Buffer a catalog-keyed photo; T02 resolves bytes only after a safe claim."""

    await context.queue_photo_presentation(
        asset_key=action.asset_key, caption=context.render(action.caption)
    )


async def show_activity(action: ShowActivityAction, context: ActionContext) -> None:
    """Request transient activity before a potentially slow local match operation."""

    await context.flush_presentation()
    chat_id = context.user.telegram_user_id
    if type(chat_id) is not int or chat_id <= 0:
        raise ValueError("a Telegram user id is required for activity")
    outcome = await context.telegram.send_activity(
        chat_id=chat_id, activity=action.activity
    )
    if not isinstance(outcome, TelegramActivityConfirmed):
        raise TypeError("Telegram activity was not confirmed")


async def save_incoming(action: SaveIncomingAction, context: ActionContext) -> None:
    """Store the one canonical Zone X exact-text match interest through F01."""

    if action.field != "human_match_request.interest" or not action.preserve_exact_text:
        raise ValueError("unsupported incoming-field persistence action")
    incoming = context.incoming
    if incoming is None or incoming.text is None:
        raise ValueError("match interest requires a user-authored text message")
    service_id = _current_service_id(context)
    if service_id is None:
        raise ValueError("match interest requires a selected service")
    request = await _normal_request(context, service_id)
    updated = await context.unit_of_work.matches.set_interest(
        request.id,
        requester_user_id=context.user.id,
        interest=incoming.text,
        now=context.now,
    )
    context.set_match_request(updated)


async def add_service_attendance(
    action: AddServiceAttendanceAction, context: ActionContext
) -> None:
    """Re-evaluate the selected service at mutation time and record late arrival safely."""

    outcome = await context.services.select_service_in_uow(
        context.unit_of_work,
        user_id=context.user.id,
        service_id=context.selected_service_id(),
        now=context.now,
    )
    if outcome.kind != action.attendance_status:
        raise ValueError("service attendance state changed before latecomer entry")


async def select_service_attendance(
    action: SelectServiceAttendanceAction, context: ActionContext
) -> None:
    """Apply a service callback choice inside the ingress transaction and emit its outcome."""

    del action
    outcome = await context.services.select_service_in_uow(
        context.unit_of_work,
        user_id=context.user.id,
        service_id=context.selected_service_id(),
        now=context.now,
    )
    await _emit_attendance_outcome(context, outcome)


async def enter_service_checkpoint(
    action: EnterServiceCheckpointAction, context: ActionContext
) -> None:
    """Delegate named checkpoint entry to the application-owned F01 transition port."""

    await context.require_navigation().enter_service_checkpoint(
        context, flow_key=action.flow_key
    )


async def enter_selected_service_checkpoint(
    action: EnterSelectedServiceCheckpointAction, context: ActionContext
) -> None:
    """Enter the selected service home checkpoint via the composed application catalog."""

    del action
    await context.require_navigation().enter_selected_service_checkpoint(context)


async def enter_selected_service_latecomer_flow(
    action: EnterSelectedServiceLatecomerFlowAction, context: ActionContext
) -> None:
    """Enter the selected service latecomer root via the composed application catalog."""

    del action
    await context.require_navigation().enter_selected_service_latecomer_flow(context)


async def resolve_service_switch_options(
    action: ResolveServiceSwitchOptionsAction, context: ActionContext
) -> None:
    """Offer only other currently open services; selection rechecks time at the click."""

    if not (
        action.preserve_historical_attendance
        and action.replace_active_overlapping_service
    ):
        raise ValueError("service switch action weakens the approved attendance policy")
    current_service_id = _current_service_id(context)
    choices = tuple(
        service
        for service in await context.unit_of_work.services.list_ongoing(now=context.now)
        if service.id != current_service_id
    )
    context.set_service_choices(choices)
    if choices:
        context.emit(ActionEvent(key="service_attendance.choice_required"))
        return
    context.emit(ActionEvent(key="service_attendance.none_available"))


async def find_and_reserve_server(
    action: FindAndReserveServerAction, context: ActionContext
) -> None:
    """Create or validate one owned normal request, then reserve an exact server once."""

    if not action.require_service_attendance or action.capacity_required != 1:
        raise ValueError("normal matching action weakens the approved match policy")
    service_id = context.render_uuid(action.service_id)
    request = await _normal_request(context, service_id)
    assignment = await context.matching.reserve_normal_in_uow(
        context.unit_of_work,
        requester_user_id=context.user.id,
        request_id=request.id,
        service_id=service_id,
        now=context.now,
    )
    await _record_match_outcome(
        context,
        request=request,
        assignment_found=assignment is not None,
        event_prefix="human_match",
    )


async def find_and_reserve_safety_responder(
    action: FindAndReserveSafetyResponderAction, context: ActionContext
) -> None:
    """Keep urgent requests in the safety-only leader/staff pool."""

    if (
        not action.require_service_attendance_or_always_available
        or action.capacity_required != 1
    ):
        raise ValueError("safety matching action weakens the approved match policy")
    service_id = context.optional_service_id(action.service_id)
    request = await _safety_request(context, service_id)
    assignment = await context.matching.reserve_safety_in_uow(
        context.unit_of_work,
        requester_user_id=context.user.id,
        request_id=request.id,
        service_id=service_id,
        now=context.now,
    )
    await _record_match_outcome(
        context,
        request=request,
        assignment_found=assignment is not None,
        event_prefix="safety_match",
    )


async def confirm_human_match(
    action: ConfirmHumanMatchAction, context: ActionContext
) -> None:
    """Persist one immutable requester-owned meeting preference before notifications."""

    request_id = context.match_request_id()
    request = await context.unit_of_work.matches.confirm(
        request_id,
        requester_user_id=context.user.id,
        meeting_preference=action.meeting_preference,
        now=context.now,
    )
    context.set_match_request(request)
    context.set_matched_responder(
        await context.unit_of_work.matches.current_responder(
            request_id, requester_user_id=context.user.id
        )
    )


async def release_human_match(
    action: ReleaseHumanMatchAction, context: ActionContext
) -> None:
    """Release the current assignment and retain its typed contact only for rematch notices."""

    del action
    released = await context.unit_of_work.matches.release_active(
        context.match_request_id(),
        requester_user_id=context.user.id,
        reason="requester_reported_not_responding",
        now=context.now,
    )
    context.set_previous_responder(released)
    context.set_matched_responder(None)


async def notify_matched_human(
    action: NotifyMatchedHumanAction, context: ActionContext
) -> None:
    """Notify only the persisted active responder, never a profile-owner surrogate."""

    responder = await _current_responder(context)
    await context.enqueue_text_to(
        user_id=responder.recipient_user_id,
        telegram_chat_id=responder.telegram_chat_id,
        text=context.render(action.text),
    )


async def notify_previous_human(
    action: NotifyPreviousHumanAction, context: ActionContext
) -> None:
    """Notify only the responder atomically released by the preceding action."""

    responder = context.previous_responder
    if responder is None:
        raise ValueError("previous human notification requires a released responder")
    await context.enqueue_text_to(
        user_id=responder.recipient_user_id,
        telegram_chat_id=responder.telegram_chat_id,
        text=context.render(action.text),
    )


async def notify_all_admins(
    action: NotifyAllAdminsAction, context: ActionContext
) -> None:
    """Create one sanitized durable admin fan-out request without raw user content."""

    LOGGER.error("safety_match outcome=no_responder")
    await context.diagnostics.record(
        correlation_id=context.correlation_id,
        severity=action.severity,
        safe_summary=context.render(action.safe_summary),
        safe_context={"reason_code": "safety_match.no_responder"},
        at=context.now,
    )


async def exclude_previous_human_from_next_attempt(
    action: ExcludePreviousHumanFromNextAttemptAction, context: ActionContext
) -> None:
    """Exclude exactly the released profile from this owned request's next reservation."""

    del action
    responder = context.previous_responder
    if responder is None:
        raise ValueError("match exclusion requires a released responder")
    await context.unit_of_work.matches.exclude_responder(
        context.match_request_id(),
        responder.profile_id,
        requester_user_id=context.user.id,
        now=context.now,
    )


async def share_human_contact(
    action: ShareHumanContactAction, context: ActionContext
) -> None:
    """Render contact only from the active persisted responder record."""

    responder = await _current_responder(context)
    if responder.telegram_contact_url is None:
        raise ValueError("matched responder has no approved Telegram contact URL")
    await context.queue_text_presentation(context.render(action.text))


async def mark_safety_request_pending(
    action: MarkSafetyRequestPendingAction, context: ActionContext
) -> None:
    """Prove the no-responder request is owned and remains pending for admin follow-up."""

    del action
    request = await context.unit_of_work.matches.require_request(
        context.match_request_id(), requester_user_id=context.user.id
    )
    if request.kind != "safety" or request.status != "pending":
        raise ValueError("safety request cannot be marked pending")
    context.set_match_request(request)


async def end_service_interactions(
    action: EndServiceInteractionsAction, context: ActionContext
) -> None:
    """Use T02 lifecycle's caller-owned-UoW operation; never duplicate expiry SQL here."""

    if not (
        action.expire_service_bound_selections
        and action.release_service_match_capacity
        and action.return_to_system_checkpoint
    ):
        raise ValueError(
            "service interaction action weakens the approved expiry policy"
        )
    service_id = _current_service_id(context)
    if service_id is None:
        raise ValueError("service interaction expiry requires a service-bound root")
    service = await context.unit_of_work.services.get(service_id)
    outcome = await context.lifecycle.end_interactions_in_uow(
        context.unit_of_work, services=(service,), now=context.now
    )
    await context.require_navigation().return_users_to_system_checkpoint(
        context, user_ids=outcome.affected_user_ids
    )


async def return_to_nearest_checkpoint(
    action: ReturnToNearestCheckpointAction, context: ActionContext
) -> None:
    """Apply the F01 transition-engine return exactly once for this branch."""

    del action
    await context.require_navigation().return_to_nearest_checkpoint(context)


async def _buttons_for(
    context: ActionContext,
    buttons: list[ButtonDefinition],
    *,
    service_bound: bool,
) -> tuple[TelegramInlineButton, ...]:
    resolved: list[TelegramInlineButton] = []
    for button in buttons:
        resolved.append(
            TelegramInlineButton(
                text=context.render(button.text),
                callback_data=await _callback_for_button(
                    context, button, service_bound=service_bound
                ),
            )
        )
    return tuple(resolved)


async def _callback_for_button(
    context: ActionContext, button: ButtonDefinition, *, service_bound: bool
) -> str:
    if button.payload is not None:
        service_id = _current_service_id(context)
        if service_id is None:
            raise ValueError("configured service callback has no selected service")
        service = await context.unit_of_work.services.get(service_id)
        if service.key != button.payload.service_key:
            raise ValueError(
                "configured service callback does not match selected service"
            )
        return encode_callback(
            TelegramCallback(
                button_id=button.button_id,
                context_kind=TelegramCallbackContextKind.SERVICE,
                context_id=service.id,
            )
        )
    request_id = context.current_match_request_id()
    if request_id is not None:
        return encode_callback(
            TelegramCallback(
                button_id=button.button_id,
                context_kind=TelegramCallbackContextKind.MATCH_REQUEST,
                context_id=request_id,
            )
        )
    if service_bound:
        service_id = _current_service_id(context)
        if service_id is None:
            raise ValueError("service-bound button has no selected service")
        return encode_callback(
            TelegramCallback(
                button_id=button.button_id,
                context_kind=TelegramCallbackContextKind.SERVICE,
                context_id=service_id,
            )
        )
    return encode_callback(TelegramCallback(button_id=button.button_id))


async def _normal_request(
    context: ActionContext, service_id: UUID
) -> MatchRequestRecord:
    existing_id = context.current_match_request_id()
    if existing_id is not None:
        request = await context.unit_of_work.matches.require_request(
            existing_id, requester_user_id=context.user.id
        )
        if request.kind != "normal" or request.service_id != service_id:
            raise ValueError(
                "match request does not belong to the selected normal service"
            )
    else:
        await context.unit_of_work.lock_user(context.user.id)
        request = await context.unit_of_work.matches.get_or_create_active_request(
            requester_user_id=context.user.id,
            service_id=service_id,
            kind="normal",
            now=context.now,
        )
    context.set_match_request(request)
    return request


async def _safety_request(
    context: ActionContext, service_id: UUID | None
) -> MatchRequestRecord:
    existing_id = context.current_match_request_id()
    if existing_id is not None:
        request = await context.unit_of_work.matches.require_request(
            existing_id, requester_user_id=context.user.id
        )
        if request.kind != "safety" or request.service_id != service_id:
            raise ValueError(
                "match request does not belong to the selected safety scope"
            )
    else:
        await context.unit_of_work.lock_user(context.user.id)
        request = await context.unit_of_work.matches.get_or_create_active_request(
            requester_user_id=context.user.id,
            service_id=service_id,
            kind="safety",
            now=context.now,
        )
    context.set_match_request(request)
    return request


async def _record_match_outcome(
    context: ActionContext,
    *,
    request: MatchRequestRecord,
    assignment_found: bool,
    event_prefix: str,
) -> None:
    responder = None
    if assignment_found:
        responder = await context.unit_of_work.matches.current_responder(
            request.id, requester_user_id=context.user.id
        )
        if responder is None:
            await context.unit_of_work.matches.release_active(
                request.id,
                requester_user_id=context.user.id,
                reason="responder_unreachable_before_notification",
                now=context.now,
            )
            assignment_found = False
    context.set_match_request(request)
    context.set_matched_responder(responder)
    context.emit(
        ActionEvent(
            key=f"{event_prefix}.found"
            if assignment_found
            else f"{event_prefix}.not_found",
            payload={"match.request_id": str(request.id)},
        )
    )


async def _current_responder(context: ActionContext) -> MatchResponderRecord:
    responder = context.matched_responder
    if responder is None:
        responder = await context.unit_of_work.matches.current_responder(
            context.match_request_id(), requester_user_id=context.user.id
        )
        context.set_matched_responder(responder)
    if responder is None:
        raise ValueError("human match has no active reachable responder")
    return responder


async def _emit_attendance_outcome(
    context: ActionContext, outcome: AttendanceOutcome
) -> None:
    if outcome.kind not in {"selected", "latecomer", "ended"}:
        raise RuntimeError(
            "selected service attendance produced an unsupported outcome"
        )
    if outcome.service_id is not None:
        context.set_local_value("service.id", str(outcome.service_id))
    context.emit(
        ActionEvent(
            key=f"service_attendance.{outcome.kind}",
            payload=(
                {"service.id": str(outcome.service_id)}
                if outcome.service_id is not None
                else {}
            ),
        )
    )


def _current_service_id(context: ActionContext) -> UUID | None:
    if context.branch.service_id is not None:
        return context.branch.service_id
    value = context.local_values.get("service.id")
    if type(value) is not str:
        return None
    try:
        return UUID(value)
    except ValueError as error:
        raise ValueError("local service id must be a UUID") from error
