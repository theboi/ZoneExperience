"""Canonical split system-global and Zone X service seed checks."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import run

from friendly_bot.app import load_system_global_seed, load_zone_x_seed
from friendly_bot.domain.actions import SendButtonsAction
from friendly_bot.domain.triggers import OnAnyOfTrigger, OnButtonPressTrigger

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_SEED_PATH = PROJECT_ROOT / "seeds" / "system-global.json"
ZONE_X_SEED_PATH = PROJECT_ROOT / "seeds" / "services" / "zone-x.json"


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
        "text": "Is there anything else I can help you with?",
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
        ("system.global.menu.timings", "When do we gather?"),
        ("system.global.menu.directions", "How to get to service?"),
        ("system.global.menu.expect", "What to expect?"),
        ("system.global.menu.zone", "What is The Zone?"),
        ("system.global.menu.connect", "Get connected"),
    ]


def test_system_root_keeps_questions_at_the_root_and_supports_schedule_follow_up() -> (
    None
):
    """A plain service name can answer a preceding timing question without a reply."""

    root = load_system_global_seed(SYSTEM_SEED_PATH).root
    root_keys = {str(flow.key) for flow in root.next_flows}

    assert {
        "system.global.menu.timings",
        "system.global.schedule.arrow",
        "system.global.about.ncc",
        "system.global.travel.drive",
        "system.global.community.small_group",
        "system.global.faith.follow_jesus",
        "system.global.venue.lost_property",
        "system.global.policy.privacy",
    } <= root_keys

    timings = next(
        flow for flow in root.next_flows if flow.key == "system.global.menu.timings"
    )
    follow_up = next(
        flow
        for flow in timings.next_flows
        if flow.key == "system.global.menu.timings.arrow"
    )

    assert isinstance(follow_up.trigger, OnAnyOfTrigger)
    assert any(
        isinstance(trigger, OnButtonPressTrigger)
        and trigger.button_id == "system.global.menu.timings.arrow"
        for trigger in follow_up.trigger.triggers
    )


def test_system_root_options_question_reopens_the_same_button_menu() -> None:
    """A typed request for options must not depend on an undocumented response."""

    root = load_system_global_seed(SYSTEM_SEED_PATH).root
    options_flow = next(
        flow for flow in root.next_flows if flow.key == "system.global.options"
    )

    assert "what options" in options_flow.trigger.llm_gist
    for flow in root.next_flows:
        if not str(flow.key).startswith("system.global.menu."):
            continue
        if not isinstance(flow.trigger, OnAnyOfTrigger):
            continue
        assert any(
            isinstance(trigger, OnButtonPressTrigger)
            for trigger in flow.trigger.triggers
        )


def test_split_json_is_semantically_equal_to_canonical_yaml() -> None:
    """Seed drift must fail before immutable roots can be published."""

    completed = run(
        [
            "ruby",
            "tests/e2e/verify_zone_x_seed.rb",
            "docs/examples/zone-x-service-example.md",
            "seeds/system-global.json",
            "seeds/services/zone-x.json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_answer_fragments_are_leaves_and_fixed_copy_is_formatting_only() -> None:
    """Answers stay presentation-only and fixed text is reserved for URLs/maps."""

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
        "http" in action["text"] or "service.map_url" in action["text"]
        for action in fixed_actions
    )


def test_zone_x_service_seed_contains_only_service_configuration() -> None:
    """Adding another service must require only another file in `seeds/services/`."""

    raw = _service_document()
    parsed = load_zone_x_seed(ZONE_X_SEED_PATH)

    assert set(raw) == {"service"}
    assert parsed.service.key == "zone_x_2026_10_18"


def _system_document() -> dict[str, object]:
    return json.loads(SYSTEM_SEED_PATH.read_text(encoding="utf-8"))


def _service_document() -> dict[str, object]:
    return json.loads(ZONE_X_SEED_PATH.read_text(encoding="utf-8"))


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
