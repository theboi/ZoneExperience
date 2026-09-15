"""Canonical Zone X source/seed equivalence checks."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import run

from friendly_bot.app import load_zone_x_seed
from friendly_bot.domain.actions import (
    ReturnToNearestCheckpointAction,
    SendButtonsAction,
)
from friendly_bot.domain.triggers import (
    AnyOfDiscussionFlowTrigger,
    ButtonDiscussionFlowTrigger,
    MessageDiscussionFlowTrigger,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_system_root_sends_its_prompt_when_opened() -> None:
    """Opening the system checkpoint must produce a useful first response."""

    seed = json.loads((PROJECT_ROOT / "seeds" / "zone-x.json").read_text())

    assert seed["system_global_root_excerpt"]["actions"][0] == {
        "type": "send_message",
        "text": "Hey {{ user.name }}! Nice to meet you! What would you like help with?",
    }
    assert seed["system_global_root_excerpt"]["return_actions"][0] == {
        "type": "send_message",
        "text": "Is there anything else I can help you with?",
    }


def test_safety_routing_requires_an_explicit_current_message_disclosure() -> None:
    """Ordinary or ambiguous help requests must not create a safety escalation."""

    seed = json.loads((PROJECT_ROOT / "seeds" / "zone-x.json").read_text())
    safety = seed["system_global_root_excerpt"]["next_flows"][1]

    assert "current message clearly says" in safety["trigger"]["llm_gist"]
    assert "ambiguous requests for help" in safety["trigger"]["llm_gist"]


def test_system_root_offers_each_supported_action_as_a_button() -> None:
    """New users can discover every system-level action without model routing."""

    root = load_zone_x_seed(
        PROJECT_ROOT / "seeds" / "zone-x.json"
    ).system_global_root_excerpt
    button_action = next(
        action for action in root.actions if isinstance(action, SendButtonsAction)
    )

    assert [(button.button_id, button.text) for button in button_action.buttons] == [
        ("system.global.menu.timings", "Service timings for each youth group"),
        ("system.global.menu.directions", "Directions to Star"),
        ("system.global.menu.expect", "What to expect"),
        ("system.global.menu.zone", "What is The Zone?"),
        ("system.global.menu.connect", "Get connected"),
    ]


def test_system_root_options_question_reopens_the_same_button_menu() -> None:
    """A typed request for options must not depend on an undocumented response."""

    root = load_zone_x_seed(
        PROJECT_ROOT / "seeds" / "zone-x.json"
    ).system_global_root_excerpt
    options_flow = next(
        flow for flow in root.next_flows if flow.key == "system.global.options"
    )

    assert isinstance(options_flow.trigger, MessageDiscussionFlowTrigger)
    assert "what options" in options_flow.trigger.llm_gist
    assert isinstance(options_flow.actions[0], ReturnToNearestCheckpointAction)

    for flow in root.next_flows:
        if not flow.key.startswith("system.global.menu."):
            continue
        assert isinstance(flow.trigger, AnyOfDiscussionFlowTrigger)
        assert any(
            isinstance(trigger, ButtonDiscussionFlowTrigger)
            for trigger in flow.trigger.triggers
        )


def test_zone_x_json_is_semantically_equal_to_canonical_yaml() -> None:
    """JSON seed drift must fail before any immutable root can be published."""

    completed = run(
        [
            "ruby",
            "tests/e2e/verify_zone_x_seed.rb",
            "docs/examples/zone-x-service-example.md",
            "seeds/zone-x.json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
