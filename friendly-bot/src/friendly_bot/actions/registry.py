"""Closed concrete-action executor registration for I04 composition."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast, get_args

from friendly_bot.actions.context import ActionContext
from friendly_bot.domain.actions import DiscussionAction, DiscussionActionBase
from friendly_bot.matching.service import MatchingService
from friendly_bot.onboarding.accounts import OperationalAccountService
from friendly_bot.persistence.repositories import DiagnosticRepository
from friendly_bot.services import ServiceAttendanceService, ServiceLifecycleService
from friendly_bot.telegram import TelegramGateway

type ActionExecutor[A: DiscussionActionBase] = Callable[
    [A, ActionContext], Awaitable[None]
]


class DuplicateActionExecutorError(ValueError):
    """Raised when composition tries to register an action class twice."""


class UnregisteredActionExecutorError(LookupError):
    """Raised when a persisted concrete action has no exact executor."""


class ActionRegistryCompletenessError(ValueError):
    """Raised when startup sees a missing or extra concrete executor."""

    def __init__(
        self,
        *,
        missing: frozenset[type[DiscussionActionBase]],
        extra: frozenset[type[DiscussionActionBase]],
    ) -> None:
        self.missing = missing
        self.extra = extra

        def names(action_types: frozenset[type[DiscussionActionBase]]) -> str:
            return ", ".join(sorted(action.__name__ for action in action_types))

        super().__init__(f"missing=[{names(missing)}] extra=[{names(extra)}]")


@dataclass(frozen=True, slots=True)
class ActionDependencies:
    """The explicit upstream services shared by concrete action executors."""

    telegram: TelegramGateway
    services: ServiceAttendanceService
    lifecycle: ServiceLifecycleService
    matching: MatchingService
    diagnostics: DiagnosticRepository | None = None
    operational_accounts: OperationalAccountService | None = None


class ActionExecutorRegistry:
    """Map only exact F01 concrete action classes to typed local executors."""

    def __init__(self) -> None:
        self._executors: dict[
            type[DiscussionActionBase], ActionExecutor[DiscussionActionBase]
        ] = {}

    def register[A: DiscussionActionBase](
        self, action_type: type[A], executor: ActionExecutor[A]
    ) -> None:
        if action_type in self._executors:
            raise DuplicateActionExecutorError(action_type.__name__)
        self._executors[action_type] = cast(
            ActionExecutor[DiscussionActionBase], executor
        )

    def resolve(self, action: DiscussionAction) -> ActionExecutor[DiscussionActionBase]:
        try:
            return self._executors[type(action)]
        except KeyError as error:
            raise UnregisteredActionExecutorError(type(action).__name__) from error

    def assert_complete(
        self, action_types: frozenset[type[DiscussionActionBase]]
    ) -> None:
        registered = frozenset(self._executors)
        if registered != action_types:
            raise ActionRegistryCompletenessError(
                missing=action_types - registered,
                extra=registered - action_types,
            )


def concrete_action_types(
    action_union: object,
) -> frozenset[type[DiscussionActionBase]]:
    """Derive the exact concrete action set from F01's discriminated union."""

    annotated_arguments = get_args(action_union)
    if not annotated_arguments:
        raise TypeError("expected an annotated F01 discussion-action union")
    action_types = frozenset(
        action_type
        for action_type in get_args(annotated_arguments[0])
        if isinstance(action_type, type)
        and issubclass(action_type, DiscussionActionBase)
    )
    if not action_types:
        raise TypeError("discussion-action union contains no concrete action classes")
    return action_types


def build_action_registry(dependencies: ActionDependencies) -> ActionExecutorRegistry:
    """Register every concrete F01 action explicitly at application composition time."""

    del dependencies
    from friendly_bot.actions.executors import (
        add_service_attendance,
        capture_operational_login_name,
        complete_operational_login,
        confirm_human_match,
        end_service_interactions,
        enter_selected_service_checkpoint,
        enter_selected_service_latecomer_flow,
        enter_service_checkpoint,
        exclude_previous_human_from_next_attempt,
        find_and_reserve_safety_responder,
        find_and_reserve_server,
        logout_operational_account,
        manage_operational_account,
        mark_safety_request_pending,
        notify_all_admins,
        notify_matched_human,
        notify_previous_human,
        release_human_match,
        resolve_service_switch_options,
        return_to_nearest_checkpoint,
        save_incoming,
        save_operational_interests,
        select_service_attendance,
        send_buttons,
        send_message_fixed,
        send_message_llm,
        send_message_paraphrased,
        send_photo,
        send_service_choice_buttons,
        share_human_contact,
        show_activity,
    )
    from friendly_bot.domain.actions import (
        AddServiceAttendanceAction,
        CaptureOperationalLoginNameAction,
        CompleteOperationalLoginAction,
        ConfirmHumanMatchAction,
        EndServiceInteractionsAction,
        EnterSelectedServiceCheckpointAction,
        EnterSelectedServiceLatecomerFlowAction,
        EnterServiceCheckpointAction,
        ExcludePreviousHumanFromNextAttemptAction,
        FindAndReserveSafetyResponderAction,
        FindAndReserveServerAction,
        LogoutOperationalAccountAction,
        ManageOperationalAccountAction,
        MarkSafetyRequestPendingAction,
        NotifyAllAdminsAction,
        NotifyMatchedHumanAction,
        NotifyPreviousHumanAction,
        ReleaseHumanMatchAction,
        ResolveServiceSwitchOptionsAction,
        ReturnToNearestCheckpointAction,
        SaveIncomingAction,
        SaveOperationalInterestsAction,
        SelectServiceAttendanceAction,
        SendButtonsAction,
        SendMessageFixedAction,
        SendMessageLlmAction,
        SendMessageParaphrasedAction,
        SendPhotoAction,
        SendServiceChoiceButtonsAction,
        ShareHumanContactAction,
        ShowActivityAction,
    )

    registry = ActionExecutorRegistry()
    registry.register(SendMessageParaphrasedAction, send_message_paraphrased)
    registry.register(SendMessageLlmAction, send_message_llm)
    registry.register(SendMessageFixedAction, send_message_fixed)
    registry.register(SendButtonsAction, send_buttons)
    registry.register(SendServiceChoiceButtonsAction, send_service_choice_buttons)
    registry.register(SendPhotoAction, send_photo)
    registry.register(ShowActivityAction, show_activity)
    registry.register(SaveIncomingAction, save_incoming)
    registry.register(CaptureOperationalLoginNameAction, capture_operational_login_name)
    registry.register(CompleteOperationalLoginAction, complete_operational_login)
    registry.register(SaveOperationalInterestsAction, save_operational_interests)
    registry.register(ManageOperationalAccountAction, manage_operational_account)
    registry.register(LogoutOperationalAccountAction, logout_operational_account)
    registry.register(AddServiceAttendanceAction, add_service_attendance)
    registry.register(SelectServiceAttendanceAction, select_service_attendance)
    registry.register(EnterServiceCheckpointAction, enter_service_checkpoint)
    registry.register(
        EnterSelectedServiceCheckpointAction, enter_selected_service_checkpoint
    )
    registry.register(
        EnterSelectedServiceLatecomerFlowAction,
        enter_selected_service_latecomer_flow,
    )
    registry.register(ResolveServiceSwitchOptionsAction, resolve_service_switch_options)
    registry.register(FindAndReserveServerAction, find_and_reserve_server)
    registry.register(
        FindAndReserveSafetyResponderAction, find_and_reserve_safety_responder
    )
    registry.register(ConfirmHumanMatchAction, confirm_human_match)
    registry.register(ReleaseHumanMatchAction, release_human_match)
    registry.register(NotifyMatchedHumanAction, notify_matched_human)
    registry.register(NotifyPreviousHumanAction, notify_previous_human)
    registry.register(NotifyAllAdminsAction, notify_all_admins)
    registry.register(
        ExcludePreviousHumanFromNextAttemptAction,
        exclude_previous_human_from_next_attempt,
    )
    registry.register(ShareHumanContactAction, share_human_contact)
    registry.register(MarkSafetyRequestPendingAction, mark_safety_request_pending)
    registry.register(EndServiceInteractionsAction, end_service_interactions)
    registry.register(ReturnToNearestCheckpointAction, return_to_nearest_checkpoint)
    registry.assert_complete(concrete_action_types(DiscussionAction))
    return registry
