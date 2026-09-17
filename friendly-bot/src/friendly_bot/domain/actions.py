"""Closed action models for persisted discussion-flow configuration."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    TypeAdapter,
)

from friendly_bot.domain.events import StableKey
from friendly_bot.domain.triggers import NonEmptyText, StableButtonId

StableAssetKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")]


class DiscussionActionBase(BaseModel):
    """Common base for configuration-only action declarations."""

    model_config = ConfigDict(extra="forbid")
    declared_event_keys: ClassVar[frozenset[str]] = frozenset()


class ButtonDefinition(BaseModel):
    """A transport-neutral button that carries only stable configuration data."""

    model_config = ConfigDict(extra="forbid")

    button_id: StableButtonId
    text: NonEmptyText
    payload: ServiceKeyButtonPayload | None = None


class ServiceKeyButtonPayload(BaseModel):
    """The one published button context that I04 may resolve to a service UUID."""

    model_config = ConfigDict(extra="forbid")

    service_key: StableKey


class SendMessageParaphrasedAction(DiscussionActionBase):
    """Requester-facing copy the LLM may paraphrase while preserving its content."""

    type: Literal["send_message_paraphrased"]
    text: NonEmptyText


class SendMessageLlmAction(DiscussionActionBase):
    """A source-grounded answer the LLM must derive only from configured prose."""

    type: Literal["send_message_llm"]
    source: NonEmptyText


class SendMessageFixedAction(DiscussionActionBase):
    """Requester-facing copy that must be sent exactly as authored."""

    type: Literal["send_message_fixed"]
    text: NonEmptyText


class SendButtonsAction(DiscussionActionBase):
    type: Literal["send_buttons"]
    service_bound: bool
    buttons: list[ButtonDefinition] = Field(min_length=1)
    text: NonEmptyText | None = None


class SendServiceChoiceButtonsAction(DiscussionActionBase):
    type: Literal["send_service_choice_buttons"]
    button_id: StableButtonId
    text: NonEmptyText
    choice_source: Literal["resolved_service_options"] = "resolved_service_options"
    service_bound: Literal[True] = True


class SendPhotoAction(DiscussionActionBase):
    type: Literal["send_photo"]
    asset_key: StableAssetKey
    caption: NonEmptyText


class ShowActivityAction(DiscussionActionBase):
    type: Literal["show_activity"]
    activity: Literal["typing"]


class SaveIncomingAction(DiscussionActionBase):
    type: Literal["save_incoming"]
    field: StableKey
    preserve_exact_text: bool


class AddServiceAttendanceAction(DiscussionActionBase):
    type: Literal["add_service_attendance"]
    attendance_status: Literal["ordinary", "latecomer"]


class SelectServiceAttendanceAction(DiscussionActionBase):
    type: Literal["select_service_attendance"]
    declared_event_keys: ClassVar[frozenset[str]] = frozenset(
        {
            "service_attendance.selected",
            "service_attendance.latecomer",
            "service_attendance.ended",
        }
    )


class EnterServiceCheckpointAction(DiscussionActionBase):
    type: Literal["enter_service_checkpoint"]
    flow_key: StableKey


class EnterSelectedServiceCheckpointAction(DiscussionActionBase):
    type: Literal["enter_selected_service_checkpoint"]


class EnterSelectedServiceLatecomerFlowAction(DiscussionActionBase):
    type: Literal["enter_selected_service_latecomer_flow"]


class ResolveServiceSwitchOptionsAction(DiscussionActionBase):
    type: Literal["resolve_service_switch_options"]
    preserve_historical_attendance: bool
    replace_active_overlapping_service: bool
    declared_event_keys: ClassVar[frozenset[str]] = frozenset(
        {"service_attendance.choice_required", "service_attendance.none_available"}
    )


class FindAndReserveServerAction(DiscussionActionBase):
    type: Literal["find_and_reserve_server"]
    service_id: NonEmptyText
    require_service_attendance: bool = False
    capacity_required: PositiveInt = 1
    rank_with: list[StableKey] | None = None
    preserve_meeting_preference: bool = False
    declared_event_keys: ClassVar[frozenset[str]] = frozenset(
        {"human_match.found", "human_match.not_found"}
    )


class FindAndReserveSafetyResponderAction(DiscussionActionBase):
    type: Literal["find_and_reserve_safety_responder"]
    service_id: NonEmptyText | None = None
    require_service_attendance_or_always_available: bool = False
    capacity_required: PositiveInt = 1
    declared_event_keys: ClassVar[frozenset[str]] = frozenset(
        {"safety_match.found", "safety_match.not_found"}
    )


class ConfirmHumanMatchAction(DiscussionActionBase):
    type: Literal["confirm_human_match"]
    meeting_preference: Literal["nbnc_joins_human", "human_joins_nbnc"]


class ReleaseHumanMatchAction(DiscussionActionBase):
    type: Literal["release_human_match"]


class NotifyMatchedHumanAction(DiscussionActionBase):
    type: Literal["notify_matched_human"]
    text: NonEmptyText


class NotifyPreviousHumanAction(DiscussionActionBase):
    type: Literal["notify_previous_human"]
    text: NonEmptyText


class NotifyAllAdminsAction(DiscussionActionBase):
    type: Literal["notify_all_admins"]
    severity: Literal["urgent"]
    safe_summary: NonEmptyText


class ExcludePreviousHumanFromNextAttemptAction(DiscussionActionBase):
    type: Literal["exclude_previous_human_from_next_attempt"]


class ShareHumanContactAction(DiscussionActionBase):
    type: Literal["share_human_contact"]
    text: NonEmptyText


class MarkSafetyRequestPendingAction(DiscussionActionBase):
    type: Literal["mark_safety_request_pending"]


class EndServiceInteractionsAction(DiscussionActionBase):
    type: Literal["end_service_interactions"]
    expire_service_bound_selections: bool
    release_service_match_capacity: bool
    return_to_system_checkpoint: bool


class ReturnToNearestCheckpointAction(DiscussionActionBase):
    type: Literal["return_to_nearest_checkpoint"]


DiscussionAction = Annotated[
    SendMessageParaphrasedAction
    | SendMessageLlmAction
    | SendMessageFixedAction
    | SendButtonsAction
    | SendServiceChoiceButtonsAction
    | SendPhotoAction
    | ShowActivityAction
    | SaveIncomingAction
    | AddServiceAttendanceAction
    | SelectServiceAttendanceAction
    | EnterServiceCheckpointAction
    | EnterSelectedServiceCheckpointAction
    | EnterSelectedServiceLatecomerFlowAction
    | ResolveServiceSwitchOptionsAction
    | FindAndReserveServerAction
    | FindAndReserveSafetyResponderAction
    | ConfirmHumanMatchAction
    | ReleaseHumanMatchAction
    | NotifyMatchedHumanAction
    | NotifyPreviousHumanAction
    | NotifyAllAdminsAction
    | ExcludePreviousHumanFromNextAttemptAction
    | ShareHumanContactAction
    | MarkSafetyRequestPendingAction
    | EndServiceInteractionsAction
    | ReturnToNearestCheckpointAction,
    Field(discriminator="type"),
]

_action_adapter: TypeAdapter[DiscussionAction] = TypeAdapter(DiscussionAction)


def parse_action(data: dict[str, object]) -> DiscussionAction:
    """Parse one persisted action using the registered discriminator set."""

    return _action_adapter.validate_python(data)
