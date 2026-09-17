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
from friendly_bot.domain.actions import (
    SendButtonsAction,
    SendMessageLlmAction,
    SendMessageParaphrasedAction,
)
from friendly_bot.domain.triggers import (
    OnAnyOfTrigger,
    OnButtonPressTrigger,
    OnMessageTrigger,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_SEED_PATH = PROJECT_ROOT / "seeds" / "system_global.py"
ZONE_X_SEED_PATH = PROJECT_ROOT / "seeds" / "services" / "zone_x.py"


def test_system_root_sends_its_prompt_when_opened() -> None:
    """Opening the system checkpoint must produce a useful first response."""

    root = _system_document()["root"]

    assert root["actions"][0] == {
        "type": "send_message_paraphrased",
        "text": (
            "Hey {{ user.name }}! Nice to meet you! Welcome to The Zone! I'm "
            "Friendly Bot, here to help you get connected to our wonderful community!"
        ),
    }
    assert root["return_actions"][0] == {
        "type": "send_message_paraphrased",
        "text": (
            "Is there anything else I can help you with? You can ask me anything "
            "and I will try my best to answer you!"
        ),
    }


def test_seeded_operational_profile_and_login_captures_are_local_only() -> None:
    """Operational login has one pre-authorized development profile and no LLM DOB step."""

    document = _system_document()
    assert document["operational_profiles"] == [
        {
        "name": "ryan the",
            "dob": "1990-01-01",
            "role": "leader",
            "interests": [],
            "cg_name": None,
            "telegram_contact_url": None,
            "always_available": False,
            "capacity": 1,
            "is_admin": True,
        }
    ]
    login = _flow(document["root"], "system.global.operational.login")
    captures = [
        flow
        for flow in _walk_flows(login)
        if flow["key"]
        in {
            "system.global.operational.login.name",
            "system.global.operational.login.dob",
            "system.global.operational.login.interests",
            "system.global.operational.manage.interests",
        }
    ]
    assert captures
    assert all(flow["trigger"] == {"type": "any_message"} for flow in captures)


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


def test_system_root_has_source_grounded_ncc_and_youth_information_flows() -> None:
    """NCC and youth information each have a distinct source-grounded root answer."""

    root = load_system_global_seed(SYSTEM_SEED_PATH).root
    root_keys = {str(flow.key) for flow in root.next_flows}

    assert {
        "system.global.information.ncc",
        "system.global.information.zone",
        "system.global.travel.drive",
        "system.global.community.small_group",
        "system.global.faith.follow_jesus",
        "system.global.venue.lost_property",
        "system.global.policy.privacy",
    } <= root_keys
    assert not any(
        key.startswith(("system.global.schedule.", "system.global.about."))
        for key in root_keys
    )

    ncc_information = next(
        flow for flow in root.next_flows if flow.key == "system.global.information.ncc"
    )
    assert isinstance(ncc_information.trigger, OnMessageTrigger)
    assert ncc_information.trigger.llm_gist == (
        "The person asks about anything related to New Creation Church (NCC) that is "
        "not specifically about its youth ministry, The Zone."
    )
    assert len(ncc_information.actions) == 1
    assert isinstance(ncc_information.actions[0], SendMessageLlmAction)
    assert "we are God's beloved" in ncc_information.actions[0].source
    assert not ncc_information.next_flows

    zone_information = next(
        flow for flow in root.next_flows if flow.key == "system.global.information.zone"
    )
    assert isinstance(zone_information.trigger, OnAnyOfTrigger)
    assert any(
        isinstance(trigger, OnButtonPressTrigger)
        and trigger.button_id == "system.global.menu.zone"
        for trigger in zone_information.trigger.triggers
    )
    assert any(
        isinstance(trigger, OnButtonPressTrigger)
        and trigger.button_id == "system.global.menu.timings"
        for trigger in zone_information.trigger.triggers
    )
    message_trigger = next(
        trigger
        for trigger in zone_information.trigger.triggers
        if isinstance(trigger, OnMessageTrigger)
    )
    assert message_trigger.llm_gist == (
        "The person asks about anything related to The Zone, or one of its youth groups "
        "DARE, Arrow or Varsity/V."
    )
    assert len(zone_information.actions) == 1
    assert isinstance(zone_information.actions[0], SendMessageLlmAction)
    assert "DARE_SERVICE_DAY" in zone_information.actions[0].source
    assert "VARSITY_SERVICE_VENUE" in zone_information.actions[0].source
    assert not zone_information.next_flows


def test_system_root_options_question_has_an_explicit_answer() -> None:
    """A typed request for options has an explicit configured response."""

    root = load_system_global_seed(SYSTEM_SEED_PATH).root
    options_flow = next(
        flow for flow in root.next_flows if flow.key == "system.global.possible_options"
    )

    assert isinstance(options_flow.trigger, OnMessageTrigger)
    assert "what are my options?" in options_flow.trigger.possible_qns
    assert len(options_flow.actions) == 1
    assert isinstance(options_flow.actions[0], SendMessageParaphrasedAction)
    assert options_flow.actions[0].text == (
        "You can ask me anything and I will try my best to answer you!"
    )
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
        "send_message_paraphrased",
    ]


def test_split_python_modules_are_semantically_equal_to_canonical_yaml() -> None:
    """Seed drift must fail before immutable roots can be published."""

    completed = run(
        [
            "ruby",
            "tests/e2e/verify_zone_x_seed.rb",
            "docs/examples/zone-x-service-example.md",
            "seeds/system_global.py",
            "SYSTEM_GLOBAL_SEED",
            "seeds/services/zone_x.py",
            "ZONE_X_SEED",
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


def test_seed_modules_must_export_a_named_object(tmp_path: Path) -> None:
    """Configuration remains a typed Python module rather than a text data file."""

    invalid_module = tmp_path / "invalid.py"
    invalid_module.write_text("INVALID_SEED = ['not an object']\n")

    with pytest.raises(SeedModuleLoadError, match="must export an object"):
        load_seed_module(invalid_module, export_name="INVALID_SEED")


def _system_document() -> dict[str, object]:
    return load_seed_module(SYSTEM_SEED_PATH, export_name="SYSTEM_GLOBAL_SEED")


def _service_document() -> dict[str, object]:
    return load_seed_module(ZONE_X_SEED_PATH, export_name="ZONE_X_SEED")


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
