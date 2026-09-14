import math

import pytest
from pydantic import ValidationError

from friendly_bot.domain.actions import parse_action
from friendly_bot.domain.events import ActionEvent


@pytest.mark.parametrize(
    "data",
    [
        {"type": "send_message", "text": "Hello"},
        {
            "type": "send_buttons",
            "service_bound": True,
            "buttons": [{"button_id": "zone_x.menu.help", "text": "Help"}],
        },
        {
            "type": "send_service_choice_buttons",
            "button_id": "service.attendance.select",
        },
        {"type": "send_photo", "asset_key": "zone_x.poster", "caption": "Come along"},
        {"type": "show_activity", "activity": "typing"},
        {
            "type": "save_incoming",
            "field": "human_match_request.interest",
            "preserve_exact_text": True,
        },
        {"type": "add_service_attendance", "attendance_status": "latecomer"},
        {"type": "select_service_attendance"},
        {"type": "enter_service_checkpoint", "flow_key": "service.zone_x.home"},
        {"type": "enter_selected_service_checkpoint"},
        {"type": "enter_selected_service_latecomer_flow"},
        {
            "type": "resolve_service_switch_options",
            "preserve_historical_attendance": True,
            "replace_active_overlapping_service": True,
        },
        {
            "type": "find_and_reserve_server",
            "service_id": "{{ service.id }}",
            "require_service_attendance": True,
            "capacity_required": 1,
        },
        {
            "type": "find_and_reserve_safety_responder",
            "service_id": "{{ active_service.id | optional }}",
            "require_service_attendance_or_always_available": True,
            "capacity_required": 1,
        },
        {"type": "confirm_human_match", "meeting_preference": "nbnc_joins_human"},
        {"type": "release_human_match"},
        {"type": "notify_matched_human", "text": "Please connect."},
        {"type": "notify_previous_human", "text": "Please reconnect."},
        {
            "type": "notify_all_admins",
            "severity": "urgent",
            "safe_summary": "No responder.",
        },
        {"type": "exclude_previous_human_from_next_attempt"},
        {"type": "share_human_contact", "text": "Contact them."},
        {"type": "mark_safety_request_pending"},
        {
            "type": "end_service_interactions",
            "expire_service_bound_selections": True,
            "release_service_match_capacity": True,
            "return_to_system_checkpoint": True,
        },
        {"type": "return_to_nearest_checkpoint"},
    ],
)
def test_all_canonical_zone_x_action_discriminators_parse(
    data: dict[str, object],
) -> None:
    assert parse_action(data).type == data["type"]


def test_event_emitting_actions_expose_only_documented_outcomes() -> None:
    assert parse_action(
        {
            "type": "find_and_reserve_server",
            "service_id": "{{ active_service.id }}",
            "capacity_required": 1,
        }
    ).declared_event_keys == frozenset({"human_match.found", "human_match.not_found"})
    assert parse_action(
        {"type": "find_and_reserve_safety_responder", "capacity_required": 1}
    ).declared_event_keys == frozenset({"safety_match.found", "safety_match.not_found"})
    assert parse_action(
        {"type": "select_service_attendance"}
    ).declared_event_keys == frozenset(
        {
            "service_attendance.selected",
            "service_attendance.latecomer",
            "service_attendance.ended",
        }
    )
    assert parse_action(
        {
            "type": "resolve_service_switch_options",
            "preserve_historical_attendance": True,
            "replace_active_overlapping_service": True,
        }
    ).declared_event_keys == frozenset(
        {"service_attendance.choice_required", "service_attendance.none_available"}
    )


def test_runtime_service_choice_buttons_have_no_embedded_service_list() -> None:
    action = parse_action(
        {
            "type": "send_service_choice_buttons",
            "button_id": "service.attendance.select",
        }
    )

    assert action.choice_source == "resolved_service_options"
    assert action.service_bound is True
    assert action.model_dump() == {
        "type": "send_service_choice_buttons",
        "button_id": "service.attendance.select",
        "choice_source": "resolved_service_options",
        "service_bound": True,
    }


def test_button_payload_is_closed_to_the_canonical_service_key_context() -> None:
    action = parse_action(
        {
            "type": "send_buttons",
            "service_bound": True,
            "buttons": [
                {
                    "button_id": "zone_x.attendance.here",
                    "text": "Check in",
                    "payload": {"service_key": "zone_x_2026_10_18"},
                }
            ],
        }
    )

    assert action.buttons[0].payload is not None
    assert action.buttons[0].payload.service_key == "zone_x_2026_10_18"

    with pytest.raises(ValidationError):
        parse_action(
            {
                "type": "send_buttons",
                "service_bound": True,
                "buttons": [
                    {
                        "button_id": "zone_x.attendance.here",
                        "text": "Check in",
                        "payload": {"arbitrary": ["untrusted", "json"]},
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "data",
    [
        {"type": "send_message", "text": ""},
        {
            "type": "send_buttons",
            "service_bound": True,
            "buttons": [{"button_id": "bad button", "text": "Help"}],
        },
        {
            "type": "send_service_choice_buttons",
            "button_id": "service.attendance.select",
            "service_ids": ["zone_x"],
        },
        {"type": "find_and_reserve_server", "service_id": "id", "capacity_required": 0},
        {"type": "unknown_action"},
    ],
)
def test_invalid_or_unregistered_actions_are_rejected(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse_action(data)


def test_action_event_requires_a_stable_key_and_json_payload() -> None:
    event = ActionEvent(
        key="human_match.found",
        payload={
            "rank": 1,
            "candidate": "Taylor",
            "details": {"scores": [1.5, None, True]},
        },
    )

    assert event.model_dump() == {
        "key": "human_match.found",
        "payload": {
            "rank": 1,
            "candidate": "Taylor",
            "details": {"scores": [1.5, None, True]},
        },
    }

    with pytest.raises(ValidationError):
        ActionEvent(key="Bad Event")


@pytest.mark.parametrize(
    "payload",
    [
        {"non_finite": math.nan},
        {"nested": {"non_finite": math.inf}},
        {"items": [{"non_finite": -math.inf}]},
    ],
)
def test_action_event_rejects_non_finite_floats_at_every_json_depth(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ActionEvent(key="human_match.found", payload=payload)  # type: ignore[arg-type]
