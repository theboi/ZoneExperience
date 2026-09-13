"""Contracts for Friendly Bot's normalized Telegram input model."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from friendly_bot.telegram.models import (
    IncomingTelegramUpdate,
    OutboundTelegramMessage,
    TelegramResponseUncertain,
    parse_update,
)


def _message_update(*, text: object = "/login@friendly_bot") -> dict[str, object]:
    return {
        "update_id": 41,
        "message": {
            "message_id": 9,
            "date": 1_789_000_000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42},
            "text": text,
        },
    }


def test_private_text_update_is_normalized_for_command_matching() -> None:
    """Removing a bot username prevents directed commands missing configured flows."""

    update = parse_update(_message_update())

    assert isinstance(update, IncomingTelegramUpdate)
    assert update.update_id == 41
    assert update.message is not None
    assert update.message.chat.id == 42
    assert update.message.chat.kind == "private"
    assert update.message.sender.id == 42
    assert update.message.text == "/login"
    assert update.message.callback_data is None


def test_callback_and_reply_are_normalized_without_raw_payload() -> None:
    """Retaining raw callback envelopes would leak unrelated Telegram data downstream."""

    update = parse_update(
        {
            "update_id": 42,
            "callback_query": {
                "from": {"id": 42},
                "data": "zone_x.attendance.here",
                "message": {
                    "message_id": 10,
                    "date": 1_789_000_001,
                    "chat": {"id": 42, "type": "private"},
                    "text": "Are you here?",
                    "reply_to_message": {"text": "Earlier bot prompt"},
                },
            },
        }
    )

    assert isinstance(update, IncomingTelegramUpdate)
    assert update.message is not None
    assert update.message.callback_data == "zone_x.attendance.here"
    assert update.message.reply_text == "Earlier bot prompt"
    assert update.message.sent_at == datetime(2026, 9, 10, 0, 26, 41, tzinfo=UTC)
    assert not hasattr(update, "raw_payload")
    assert not hasattr(update.message, "raw_payload")


@pytest.mark.parametrize(
    "raw_update",
    [
        {"update_id": True},
        {"update_id": 1, "message": []},
        _message_update(text=""),
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1,
                "chat": {"id": 1, "type": "group"},
                "from": {"id": 1},
                "text": "hello",
            },
        },
        {
            "update_id": 1,
            "callback_query": {
                "from": {"id": 1},
                "data": "",
                "message": {
                    "message_id": 1,
                    "date": 1,
                    "chat": {"id": 1, "type": "private"},
                    "text": "prompt",
                },
            },
        },
    ],
)
def test_malformed_or_unsupported_updates_are_uncertain(
    raw_update: object,
) -> None:
    """Trusting malformed, group, or empty-content input could cross the private boundary."""

    assert parse_update(raw_update) == TelegramResponseUncertain(
        "telegram_response_malformed"
    )


@pytest.mark.parametrize(
    ("chat_id", "text", "idempotency_key"),
    [
        (0, "Hello", "delivery-1"),
        (True, "Hello", "delivery-1"),
        (42, "", "delivery-1"),
        (42, True, "delivery-1"),
        (42, "Hello", ""),
    ],
)
def test_outbound_message_rejects_invalid_transport_fields(
    chat_id: object, text: object, idempotency_key: object
) -> None:
    """Invalid route, content, or operation identity must never enter a Bot API call."""

    with pytest.raises(ValueError):
        OutboundTelegramMessage(
            chat_id=chat_id,  # type: ignore[arg-type]
            text=text,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,  # type: ignore[arg-type]
        )
