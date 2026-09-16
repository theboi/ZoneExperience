import pytest
from pydantic import ValidationError

from friendly_bot.domain.triggers import parse_trigger


def test_registered_trigger_forms_parse_their_canonical_fields() -> None:
    message = parse_trigger(
        {
            "type": "message",
            "possible_qns": ["What time is Arrow?", "When does Arrow start?"],
        }
    )
    assert message.type == "message"
    assert message.llm_gist is None
    assert message.possible_qns == ["What time is Arrow?", "When does Arrow start?"]
    assert (
        parse_trigger({"type": "button", "button_id": "zone_x.menu.help"}).type
        == "button"
    )
    assert parse_trigger({"type": "command", "command": "/start"}).type == "command"
    assert parse_trigger({"type": "automatic"}).type == "automatic"
    assert (
        parse_trigger({"type": "action_event", "event_key": "human_match.found"}).type
        == "action_event"
    )


@pytest.mark.parametrize(
    "data",
    [
        {"type": "message", "llm_gist": ""},
        {"type": "message", "possible_qns": []},
        {"type": "message"},
        {"type": "button", "button_id": "Bad Button"},
        {"type": "command", "command": "start"},
        {"type": "command", "command": "/start@friendly_bot"},
        {"type": "action_event", "event_key": "Bad Event"},
    ],
)
def test_invalid_stored_trigger_values_are_rejected(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse_trigger(data)


def test_any_of_requires_distinct_non_nested_registered_triggers() -> None:
    trigger = parse_trigger(
        {
            "type": "any_of",
            "triggers": [
                {"type": "button", "button_id": "zone_x.menu.help"},
                {"type": "message", "llm_gist": "Asks for help."},
            ],
        }
    )

    assert [child.type for child in trigger.triggers] == ["button", "message"]


@pytest.mark.parametrize(
    "data",
    [
        {
            "type": "any_of",
            "triggers": [{"type": "button", "button_id": "zone_x.menu.help"}],
        },
        {
            "type": "any_of",
            "triggers": [
                {"type": "button", "button_id": "zone_x.menu.help"},
                {"type": "button", "button_id": "zone_x.menu.help"},
            ],
        },
        {
            "type": "any_of",
            "triggers": [{"type": "any_of", "triggers": []}, {"type": "automatic"}],
        },
        {"type": "unknown"},
    ],
)
def test_any_of_rejects_insufficient_duplicate_nested_or_unknown_triggers(
    data: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        parse_trigger(data)
