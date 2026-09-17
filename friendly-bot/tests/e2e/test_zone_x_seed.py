"""Canonical split system-global and Zone X service seed checks."""

from __future__ import annotations

from pathlib import Path
from subprocess import run

import pytest

from friendly_bot.app import (
    SeedModuleLoadError,
    load_seed_module,
    load_system_global_seed,
    load_zone_x_seed,
)
from friendly_bot.domain.actions import SendButtonsAction
from friendly_bot.domain.triggers import (
    OnAnyOfTrigger,
    OnButtonPressTrigger,
    OnMessageTrigger,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_SEED_PATH = PROJECT_ROOT / "seeds" / "system-global.ts"
ZONE_X_SEED_PATH = PROJECT_ROOT / "seeds" / "services" / "zone-x.ts"


def test_system_root_sends_its_prompt_when_opened() -> None:
    """Opening the system checkpoint must produce a useful first response."""

    root = _system_document()["root"]

    assert root["actions"][0] == {
        "type": "send_message",
        "text": (
            "Hey {{ user.name }}! Nice to meet you! Welcome to The Zone! I'm "
            "Friendly Bot, here to help you get connected to our wonderful community!"
        ),
    }
    assert root["return_actions"][0] == {
        "type": "send_message",
        "text": "Is there anything else I can help you with? (you can ask me any question!)",
    }


def test_safety_routing_requires_an_explicit_current_message_disclosure() -> None:
    """Ordinary or ambiguous help requests must not create a safety escalation."""

    safety = _flow(_system_document()["root"], "system.global.safety")

    assert "current message clearly says" in safety["trigger"]["llm_gist"]
    assert "ambiguous requests for help" in safety["trigger"]["llm_gist"]


def test_system_root_offers_each_supported_action_as_a_button() -> None:
    """New users can discover the core system-level actions without model routing."""

    root = load_system_global_seed(SYSTEM_SEED_PATH).root
    button_action = next(
        action for action in root.actions if isinstance(action, SendButtonsAction)
    )

    assert [(button.button_id, button.text) for button in button_action.buttons] == [
        ("system.global.menu.timings", "when do we gather?"),
        ("system.global.menu.directions", "how to get to service?"),
        ("system.global.menu.expect", "what to expect?"),
        ("system.global.menu.zone", "what is The Zone?"),
        ("system.global.menu.connect", "get connected!"),
    ]


def test_system_root_keeps_questions_at_the_root_and_supports_schedule_follow_up() -> (
    None
):
    """A plain youth-group name can answer a preceding timing question."""

    root = load_system_global_seed(SYSTEM_SEED_PATH).root
    root_keys = {str(flow.key) for flow in root.next_flows}

    assert {
        "system.global.menu.timings",
        "system.global.schedule.dare",
        "system.global.schedule.arrow",
        "system.global.schedule.varsity",
        "system.global.about.ncc",
        "system.global.about.zone",
        "system.global.about.dare",
        "system.global.about.arrow",
        "system.global.about.varsity",
        "system.global.travel.drive",
        "system.global.community.small_group",
        "system.global.faith.follow_jesus",
        "system.global.venue.lost_property",
        "system.global.policy.privacy",
    } <= root_keys

    timings = next(
        flow for flow in root.next_flows if flow.key == "system.global.menu.timings"
    )
    assert not any(isinstance(action, SendButtonsAction) for action in timings.actions)
    assert timings.actions[0].text == (
        "we have different youth groups for different ages! which are you referring to?"
    )
    assert timings.actions[1].type == "send_message_fixed"
    assert timings.actions[1].text == (
        "DARE: for secondary school students aged 13-17yo\n"
        "Arrow: for post-secondary school students and NSFs aged 17-23yo\n"
        "Varsity: for university students"
    )

    for group in ("dare", "arrow", "varsity"):
        follow_up = next(
            flow
            for flow in timings.next_flows
            if flow.key == f"system.global.menu.timings.{group}"
        )
        assert isinstance(follow_up.trigger, OnMessageTrigger)
        assert group.capitalize() in follow_up.trigger.possible_qns

    zone = next(
        flow for flow in root.next_flows if flow.key == "system.global.about.zone"
    )
    assert isinstance(zone.trigger, OnAnyOfTrigger)
    assert any(
        isinstance(trigger, OnButtonPressTrigger)
        and trigger.button_id == "system.global.menu.zone"
        for trigger in zone.trigger.triggers
    )


def test_system_root_options_question_reopens_the_same_button_menu() -> None:
    """A typed request for options must not depend on an undocumented response."""

    root = load_system_global_seed(SYSTEM_SEED_PATH).root
    options_flow = next(
        flow for flow in root.next_flows if flow.key == "system.global.options"
    )

    assert isinstance(options_flow.trigger, OnMessageTrigger)
    assert "what are my options?" in options_flow.trigger.possible_qns
    for flow in root.next_flows:
        if not str(flow.key).startswith("system.global.menu."):
            continue
        if not isinstance(flow.trigger, OnAnyOfTrigger):
            continue
        assert any(
            isinstance(trigger, OnButtonPressTrigger)
            for trigger in flow.trigger.triggers
        )


def test_buttons_only_offer_choices_that_are_not_already_stated() -> None:
    """Known one-step actions and listed choices should be typed, not buttoned."""

    roots = [
        _system_document()["root"],
        _service_document()["service"]["service_global_root"],
        _service_document()["service"]["latecomer_flow"],
        *(
            timestamp["root_flow"]
            for timestamp in _service_document()["service"]["timestamps"]
        ),
    ]
    button_flow_keys = {
        flow["key"]
        for root in roots
        for flow in _walk_flows(root)
        if any(action["type"] == "send_buttons" for action in flow["actions"])
    }

    assert button_flow_keys == {
        "system.global",
        "service.zone_x.home",
        "service.zone_x.connect.match_found",
        "service.zone_x.timestamp.marketing",
        "service.zone_x.timestamp.service_questions",
        "service.zone_x.timestamp.after_service",
    }
    latecomer_actions = _service_document()["service"]["latecomer_flow"]["actions"]
    assert [action["type"] for action in latecomer_actions] == [
        "add_service_attendance",
        "enter_service_checkpoint",
        "send_message",
    ]


def test_split_typescript_modules_are_semantically_equal_to_canonical_yaml() -> None:
    """Seed drift must fail before immutable roots can be published."""

    completed = run(
        [
            "ruby",
            "tests/e2e/verify_zone_x_seed.rb",
            "docs/examples/zone-x-service-example.md",
            "seeds/system-global.ts",
            "seeds/services/zone-x.ts",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_answer_fragments_are_leaves_and_fixed_copy_is_formatting_only() -> None:
    """Answers stay presentation-only and fixed text preserves links or layout."""

    roots = [
        _system_document()["root"],
        _service_document()["service"]["service_global_root"],
        _service_document()["service"]["latecomer_flow"],
        *(
            timestamp["root_flow"]
            for timestamp in _service_document()["service"]["timestamps"]
        ),
    ]
    flows = [flow for root in roots for flow in _walk_flows(root)]

    assert all(
        not flow["next_flows"]
        for flow in flows
        if flow.get("multi_intent_mode") == "answer"
    )
    fixed_actions = [
        action
        for flow in flows
        for action in flow["actions"]
        if action["type"] == "send_message_fixed"
    ]
    assert fixed_actions
    assert all(
        "http" in action["text"]
        or "service.map_url" in action["text"]
        or action["text"].startswith("DARE:")
        for action in fixed_actions
    )


def test_zone_x_service_seed_contains_only_service_configuration() -> None:
    """Adding another service must require only another file in `seeds/services/`."""

    raw = _service_document()
    parsed = load_zone_x_seed(ZONE_X_SEED_PATH)

    assert set(raw) == {"service"}
    assert parsed.service.key == "zone_x_2026_10_18"


def test_seed_modules_must_default_export_an_object(tmp_path: Path) -> None:
    """Configuration remains a TypeScript module rather than a JSON-shaped text file."""

    invalid_module = tmp_path / "invalid.ts"
    invalid_module.write_text("export default ['not an object'] as const;\n")

    with pytest.raises(SeedModuleLoadError, match="could not load"):
        load_seed_module(invalid_module)


def _system_document() -> dict[str, object]:
    return load_seed_module(SYSTEM_SEED_PATH)


def _service_document() -> dict[str, object]:
    return load_seed_module(ZONE_X_SEED_PATH)


def _flow(root: dict[str, object], key: str) -> dict[str, object]:
    return next(flow for flow in _walk_flows(root) if flow["key"] == key)


def _walk_flows(root: dict[str, object]) -> list[dict[str, object]]:
    flows: list[dict[str, object]] = []
    stack = [root]
    while stack:
        flow = stack.pop()
        flows.append(flow)
        stack.extend(reversed(flow["next_flows"]))
    return flows
