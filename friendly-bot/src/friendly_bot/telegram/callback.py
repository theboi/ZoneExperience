"""Closed, size-bounded Telegram callback data codec."""

from __future__ import annotations

import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

_STABLE_BUTTON_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_MAX_CALLBACK_DATA_BYTES = 64
_VERSION = "1"


class TelegramCallbackContextKind(StrEnum):
    """The local authoritative record required before a contextual action runs."""

    SERVICE = "service"
    MATCH_REQUEST = "match_request"


@dataclass(frozen=True, slots=True)
class TelegramCallback:
    """Decoded input contains only a stable button and optional typed local context."""

    button_id: str
    context_kind: TelegramCallbackContextKind | None = None
    context_id: UUID | None = None

    def __post_init__(self) -> None:
        if _STABLE_BUTTON_ID.fullmatch(self.button_id) is None:
            raise ValueError("Telegram callback button id is invalid")
        if (self.context_kind is None) != (self.context_id is None):
            raise ValueError("Telegram callback context must have kind and id together")
        if self.context_kind is not None and not isinstance(
            self.context_kind, TelegramCallbackContextKind
        ):
            raise ValueError("Telegram callback context kind is invalid")
        if self.context_id is not None and not isinstance(self.context_id, UUID):
            raise ValueError("Telegram callback context id is invalid")


def encode_callback(callback: TelegramCallback) -> str:
    """Encode one closed callback form and enforce Telegram's byte limit."""

    if callback.context_kind is None:
        encoded = callback.button_id
    else:
        assert callback.context_id is not None
        kind = {
            TelegramCallbackContextKind.SERVICE: "s",
            TelegramCallbackContextKind.MATCH_REQUEST: "m",
        }[callback.context_kind]
        encoded = "|".join(
            (_VERSION, callback.button_id, kind, _encode_uuid(callback.context_id))
        )
    _validate_wire_length(encoded)
    return encoded


def decode_callback(value: str) -> TelegramCallback:
    """Decode only a complete typed callback; callers treat all output as hostile."""

    if type(value) is not str:
        raise ValueError("Telegram callback data must be text")
    _validate_wire_length(value)
    if "|" not in value:
        return TelegramCallback(button_id=value)

    version, button_id, kind, encoded_id = _split_contextual(value)
    if version != _VERSION:
        raise ValueError("Telegram callback version is unsupported")
    context_kind = {
        "s": TelegramCallbackContextKind.SERVICE,
        "m": TelegramCallbackContextKind.MATCH_REQUEST,
    }.get(kind)
    if context_kind is None:
        raise ValueError("Telegram callback context kind is invalid")
    return TelegramCallback(button_id, context_kind, _decode_uuid(encoded_id))


def _split_contextual(value: str) -> tuple[str, str, str, str]:
    parts = value.split("|")
    if len(parts) != 4 or any(not part for part in parts):
        raise ValueError("Telegram callback format is invalid")
    return parts[0], parts[1], parts[2], parts[3]


def _encode_uuid(value: UUID) -> str:
    return urlsafe_b64encode(value.bytes).decode("ascii").rstrip("=")


def _decode_uuid(value: str) -> UUID:
    if len(value) != 22 or any(character not in _BASE64URL for character in value):
        raise ValueError("Telegram callback context id is invalid")
    try:
        decoded = urlsafe_b64decode(value + "==")
    except ValueError as error:
        raise ValueError("Telegram callback context id is invalid") from error
    if len(decoded) != 16:
        raise ValueError("Telegram callback context id is invalid")
    result = UUID(bytes=decoded)
    if _encode_uuid(result) != value:
        raise ValueError("Telegram callback context id is invalid")
    return result


def _validate_wire_length(value: str) -> None:
    size = len(value.encode("utf-8"))
    if not 1 <= size <= _MAX_CALLBACK_DATA_BYTES:
        raise ValueError("Telegram callback data must contain 1 to 64 UTF-8 bytes")


_BASE64URL = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)
