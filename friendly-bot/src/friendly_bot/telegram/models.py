"""Minimal, strict Telegram DTOs with no retained API envelopes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypeGuard

from friendly_bot.telegram.callback import TelegramCallback, decode_callback

type TelegramUncertainCode = Literal[
    "telegram_transport_error", "telegram_response_malformed"
]

_COMMAND_WITH_RECIPIENT = re.compile(r"^/([A-Za-z0-9_]+)@[A-Za-z0-9_]+(?=$|\s)")


@dataclass(frozen=True, slots=True)
class TelegramUser:
    """The only sender fact required by the Telegram boundary."""

    id: int

    def __post_init__(self) -> None:
        if not _positive_int(self.id):
            raise ValueError("Telegram user id must be positive")


@dataclass(frozen=True, slots=True)
class TelegramChat:
    """A private Telegram chat accepted by Friendly Bot."""

    id: int
    kind: Literal["private"]

    def __post_init__(self) -> None:
        if not _positive_int(self.id):
            raise ValueError("Telegram chat id must be positive")
        if self.kind != "private":
            raise ValueError("Friendly Bot accepts private Telegram chats only")


@dataclass(frozen=True, slots=True)
class TelegramMessage:
    """Normalized private Telegram input with only fields used by Friendly Bot."""

    message_id: int
    sent_at: datetime
    chat: TelegramChat
    sender: TelegramUser
    text: str | None
    reply_text: str | None
    callback: TelegramCallback | None

    def __post_init__(self) -> None:
        if not _positive_int(self.message_id):
            raise ValueError("Telegram message id must be positive")
        if self.text is not None and (type(self.text) is not str or not self.text):
            raise ValueError("Telegram text must be nonempty when present")
        if self.reply_text is not None and (
            type(self.reply_text) is not str or not self.reply_text
        ):
            raise ValueError("Telegram reply text must be nonempty when present")
        if self.callback is not None and not isinstance(
            self.callback, TelegramCallback
        ):
            raise ValueError("Telegram callback must be typed when present")
        if self.text is None and self.callback is None:
            raise ValueError("Telegram input needs text or callback data")


@dataclass(frozen=True, slots=True)
class IncomingTelegramUpdate:
    """A Telegram update that is either processable input or safely ignorable."""

    update_id: int
    message: TelegramMessage | None

    def __post_init__(self) -> None:
        if not _positive_int(self.update_id):
            raise ValueError("Telegram update id must be positive")


@dataclass(frozen=True, slots=True)
class TelegramUpdates:
    """A completely parsed batch for one caller-owned polling offset."""

    updates: tuple[IncomingTelegramUpdate, ...]

    def __post_init__(self) -> None:
        if type(self.updates) is not tuple or not all(
            isinstance(update, IncomingTelegramUpdate) for update in self.updates
        ):
            raise ValueError("Telegram updates must be a tuple of incoming updates")


@dataclass(frozen=True, slots=True)
class OutboundTelegramMessage:
    """An immutable text operation that the durable delivery worker may send once."""

    chat_id: int
    text: str
    idempotency_key: str
    buttons: tuple[TelegramInlineButton, ...] = ()

    def __post_init__(self) -> None:
        if not _positive_int(self.chat_id):
            raise ValueError("Telegram chat id must be positive")
        if type(self.text) is not str or not self.text:
            raise ValueError("Telegram text must be nonempty plain text")
        if type(self.idempotency_key) is not str or not self.idempotency_key:
            raise ValueError("Telegram idempotency key must be nonempty")
        if type(self.buttons) is not tuple or not all(
            isinstance(button, TelegramInlineButton) for button in self.buttons
        ):
            raise ValueError("Telegram buttons must be an immutable typed tuple")


@dataclass(frozen=True, slots=True)
class TelegramInlineButton:
    """One inline keyboard button reduced to display copy and a bounded callback."""

    text: str
    callback_data: str

    def __post_init__(self) -> None:
        if type(self.text) is not str or not self.text:
            raise ValueError("Telegram inline button text must be nonempty")
        if type(self.callback_data) is not str or not self.callback_data:
            raise ValueError("Telegram inline button callback data must be nonempty")
        callback_size = len(self.callback_data.encode("utf-8"))
        if not 1 <= callback_size <= 64:
            raise ValueError("Telegram inline button callback data exceeds 64 bytes")


@dataclass(frozen=True, slots=True)
class ResolvedTelegramPhoto:
    """A verified local photo with no retained source path."""

    filename: str
    media_type: Literal["image/png", "image/jpeg"]
    content: bytes

    def __post_init__(self) -> None:
        if (
            type(self.filename) is not str
            or not self.filename
            or "/" in self.filename
            or "\\" in self.filename
        ):
            raise ValueError("Telegram photo filename must be a basename")
        if self.media_type not in {"image/png", "image/jpeg"}:
            raise ValueError("Telegram photo media type is unsupported")
        if type(self.content) is not bytes or not self.content:
            raise ValueError("Telegram photo content must be nonempty bytes")


@dataclass(frozen=True, slots=True)
class OutboundTelegramPhoto:
    """An immutable resolved-photo operation that the durable worker may send once."""

    chat_id: int
    photo: ResolvedTelegramPhoto
    caption: str
    idempotency_key: str
    buttons: tuple[TelegramInlineButton, ...] = ()

    def __post_init__(self) -> None:
        if not _positive_int(self.chat_id):
            raise ValueError("Telegram chat id must be positive")
        if not isinstance(self.photo, ResolvedTelegramPhoto):
            raise TypeError("Telegram photo must be resolved before sending")
        if type(self.caption) is not str or not self.caption:
            raise ValueError("Telegram photo caption must be nonempty plain text")
        if type(self.idempotency_key) is not str or not self.idempotency_key:
            raise ValueError("Telegram idempotency key must be nonempty")
        if type(self.buttons) is not tuple or not all(
            isinstance(button, TelegramInlineButton) for button in self.buttons
        ):
            raise ValueError("Telegram buttons must be an immutable typed tuple")


@dataclass(frozen=True, slots=True)
class TelegramActivityConfirmed:
    """Telegram accepted one ephemeral activity indicator."""


@dataclass(frozen=True, slots=True)
class TelegramSendConfirmed:
    """A successful receipt reduced to its positive Telegram message identifier."""

    message_id: int

    def __post_init__(self) -> None:
        if not _positive_int(self.message_id):
            raise ValueError("Telegram message id must be positive")


@dataclass(frozen=True, slots=True)
class TelegramSendRetry:
    """A parsed definite non-send that may be retried by the durable worker."""

    error_code: int
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if not _positive_int(self.error_code):
            raise ValueError("Telegram error code must be positive")
        if self.retry_after_seconds is not None and not _positive_int(
            self.retry_after_seconds
        ):
            raise ValueError("Telegram retry-after must be positive when present")


@dataclass(frozen=True, slots=True)
class TelegramSendRejected:
    """A parsed permanent Telegram 4xx rejection."""

    error_code: int

    def __post_init__(self) -> None:
        if type(self.error_code) is not int or not 400 <= self.error_code <= 499:
            raise ValueError("Telegram rejection must have a 4xx error code")


@dataclass(frozen=True, slots=True)
class TelegramResponseUncertain:
    """An API result that cannot safely prove whether Telegram accepted a send."""

    code: TelegramUncertainCode


TelegramSendUncertain = TelegramResponseUncertain


@dataclass(frozen=True, slots=True)
class TelegramApiError:
    """A complete API rejection reduced to its stable HTTP/Telgram error code."""

    error_code: int

    def __post_init__(self) -> None:
        if type(self.error_code) is not int or not 400 <= self.error_code <= 599:
            raise ValueError("Telegram API error must have a 4xx or 5xx error code")


@dataclass(frozen=True, slots=True)
class TelegramWebhookInfo:
    """The webhook state needed for preflight without retaining the webhook URL."""

    configured: bool

    def __post_init__(self) -> None:
        if type(self.configured) is not bool:
            raise ValueError("Telegram webhook state must be boolean")


@dataclass(frozen=True, slots=True)
class TelegramWebhookCleared:
    """A successful non-destructive Telegram webhook-clear confirmation."""


type TelegramSendOutcome = (
    TelegramSendConfirmed
    | TelegramSendRetry
    | TelegramSendRejected
    | TelegramSendUncertain
)
type OutboundTelegramRequest = OutboundTelegramMessage | OutboundTelegramPhoto
type TelegramActivityOutcome = (
    TelegramActivityConfirmed
    | TelegramSendRetry
    | TelegramSendRejected
    | TelegramSendUncertain
)
type TelegramApiFailure = TelegramApiError | TelegramResponseUncertain


def parse_update(value: object) -> IncomingTelegramUpdate | TelegramResponseUncertain:
    """Strictly reduce one supported Telegram update without retaining its envelope."""

    if not isinstance(value, dict):
        return _malformed()
    update_id = value.get("update_id")
    if not _positive_int(update_id):
        return _malformed()

    has_message = "message" in value
    has_callback = "callback_query" in value
    if has_message and has_callback:
        return _malformed()
    if has_callback:
        return _parse_callback_update(update_id, value["callback_query"])
    if not has_message:
        return IncomingTelegramUpdate(update_id=update_id, message=None)
    return _parse_message_update(update_id, value["message"])


def normalize_command(text: str) -> str:
    """Remove Telegram's optional bot recipient suffix without changing user content."""

    match = _COMMAND_WITH_RECIPIENT.match(text)
    if match is None:
        return text
    return f"/{match.group(1)}{text[match.end() :]}"


def _parse_message_update(
    update_id: int, value: object
) -> IncomingTelegramUpdate | TelegramResponseUncertain:
    if not isinstance(value, dict):
        return _malformed()
    sender = _parse_sender(value.get("from"))
    if isinstance(sender, TelegramResponseUncertain):
        return sender
    message = _parse_message(value, sender=sender, callback=None, require_text=True)
    if isinstance(message, TelegramResponseUncertain):
        return message
    return IncomingTelegramUpdate(update_id=update_id, message=message)


def _parse_callback_update(
    update_id: int, value: object
) -> IncomingTelegramUpdate | TelegramResponseUncertain:
    if not isinstance(value, dict):
        return _malformed()
    sender = _parse_sender(value.get("from"))
    if isinstance(sender, TelegramResponseUncertain):
        return sender
    raw_callback_data = value.get("data")
    if type(raw_callback_data) is not str or not raw_callback_data:
        return _malformed()
    try:
        callback = decode_callback(raw_callback_data)
    except ValueError:
        return _malformed()
    message = _parse_message(
        value.get("message"),
        sender=sender,
        callback=callback,
        require_text=False,
    )
    if isinstance(message, TelegramResponseUncertain):
        return message
    return IncomingTelegramUpdate(update_id=update_id, message=message)


def _parse_sender(value: object) -> TelegramUser | TelegramResponseUncertain:
    if not isinstance(value, dict):
        return _malformed()
    sender_id = value.get("id")
    if not _positive_int(sender_id):
        return _malformed()
    return TelegramUser(id=sender_id)


def _parse_message(
    value: object,
    *,
    sender: TelegramUser,
    callback: TelegramCallback | None,
    require_text: bool,
) -> TelegramMessage | TelegramResponseUncertain:
    if not isinstance(value, dict):
        return _malformed()
    message_id = value.get("message_id")
    sent_at = value.get("date")
    chat = _parse_chat(value.get("chat"))
    if (
        not _positive_int(message_id)
        or type(sent_at) is not int
        or sent_at < 0
        or isinstance(chat, TelegramResponseUncertain)
    ):
        return _malformed()

    text = value.get("text")
    if text is not None and (type(text) is not str or not text):
        return _malformed()
    if require_text and (type(text) is not str or not text):
        return _malformed()
    reply_text = _parse_reply_text(value.get("reply_to_message"))
    if isinstance(reply_text, TelegramResponseUncertain):
        return reply_text
    try:
        return TelegramMessage(
            message_id=message_id,
            sent_at=datetime.fromtimestamp(sent_at, UTC),
            chat=chat,
            sender=sender,
            text=normalize_command(text) if isinstance(text, str) else None,
            reply_text=reply_text,
            callback=callback,
        )
    except (OverflowError, OSError, ValueError):
        return _malformed()


def _parse_chat(value: object) -> TelegramChat | TelegramResponseUncertain:
    if not isinstance(value, dict):
        return _malformed()
    chat_id = value.get("id")
    kind = value.get("type")
    if not _positive_int(chat_id) or kind != "private":
        return _malformed()
    return TelegramChat(id=chat_id, kind="private")


def _parse_reply_text(value: object) -> str | None | TelegramResponseUncertain:
    if value is None:
        return None
    if not isinstance(value, dict):
        return _malformed()
    reply_text = value.get("text")
    if reply_text is None:
        return None
    if type(reply_text) is not str or not reply_text:
        return _malformed()
    return reply_text


def _malformed() -> TelegramResponseUncertain:
    return TelegramResponseUncertain("telegram_response_malformed")


def _positive_int(value: object) -> TypeGuard[int]:
    return type(value) is int and value > 0
