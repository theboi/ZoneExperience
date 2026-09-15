"""Canonical Zone X source/seed equivalence checks."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import run

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_system_root_sends_its_prompt_when_opened() -> None:
    """Opening the system checkpoint must produce a useful first response."""

    seed = json.loads((PROJECT_ROOT / "seeds" / "zone-x.json").read_text())

    assert seed["system_global_root_excerpt"]["actions"] == [
        {
            "type": "send_message",
            "text": "Hey {{ user.name }}! Nice to meet you! What would you like help with?",
        }
    ]
    assert seed["system_global_root_excerpt"]["return_actions"] == [
        {"type": "send_message", "text": "Is there anything else I can help you with?"}
    ]


def test_safety_routing_requires_an_explicit_current_message_disclosure() -> None:
    """Ordinary or ambiguous help requests must not create a safety escalation."""

    seed = json.loads((PROJECT_ROOT / "seeds" / "zone-x.json").read_text())
    safety = seed["system_global_root_excerpt"]["next_flows"][1]

    assert "current message clearly says" in safety["trigger"]["llm_gist"]
    assert "ambiguous requests for help" in safety["trigger"]["llm_gist"]


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
