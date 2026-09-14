"""Closed Telegram callback encoding and inbound normalization contracts."""

from __future__ import annotations

from uuid import UUID

import pytest

from friendly_bot.telegram.callback import (
    TelegramCallback,
    TelegramCallbackContextKind,
    decode_callback,
    encode_callback,
)


def test_static_and_contextual_callbacks_round_trip_without_raw_json() -> None:
    service_id = UUID("d0a7c559-a466-420b-b1cf-70c8966271ad")
    match_request_id = UUID("2da9d0c4-9909-4da5-8c52-8f6174dfec3d")

    assert decode_callback(
        encode_callback(TelegramCallback("zone_x.menu.connect"))
    ) == (TelegramCallback("zone_x.menu.connect"))
    assert decode_callback(
        encode_callback(
            TelegramCallback(
                "service.attendance.select",
                TelegramCallbackContextKind.SERVICE,
                service_id,
            )
        )
    ) == TelegramCallback(
        "service.attendance.select", TelegramCallbackContextKind.SERVICE, service_id
    )
    assert decode_callback(
        encode_callback(
            TelegramCallback(
                "zone_x.connect.not_responding",
                TelegramCallbackContextKind.MATCH_REQUEST,
                match_request_id,
            )
        )
    ) == TelegramCallback(
        "zone_x.connect.not_responding",
        TelegramCallbackContextKind.MATCH_REQUEST,
        match_request_id,
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1|zone_x.menu.connect|x|0",
        "2|zone_x.menu.connect|s|0",
        "1|bad button|s|0",
        "1|zone_x.menu.connect|s|not-a-uuid",
        "x" * 65,
        "🙂" * 17,
    ],
)
def test_callback_codec_rejects_malformed_or_over_limit_wire_values(value: str) -> None:
    with pytest.raises(ValueError):
        decode_callback(value)


def test_callback_codec_refuses_to_encode_an_over_limit_stable_button_id() -> None:
    with pytest.raises(ValueError):
        encode_callback(TelegramCallback("x." + "a" * 63))
