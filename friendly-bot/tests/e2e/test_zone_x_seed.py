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
        {"type": "send_message", "text": "What would you like help with?"}
    ]


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
