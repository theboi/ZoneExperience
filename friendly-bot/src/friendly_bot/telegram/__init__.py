"""Strict Telegram Bot API boundary for Friendly Bot."""

from typing import Protocol

from friendly_bot.telegram.assets import (
    LocalTelegramAssetResolver,
    TelegramAssetResolver,
)
from friendly_bot.telegram.callback import (
    TelegramCallback,
    TelegramCallbackContextKind,
    decode_callback,
    encode_callback,
)
from friendly_bot.telegram.client import TelegramApiClient
from friendly_bot.telegram.models import (
    IncomingTelegramUpdate,
    OutboundTelegramMessage,
    OutboundTelegramPhoto,
    OutboundTelegramRequest,
    ResolvedTelegramPhoto,
    TelegramActivityConfirmed,
    TelegramActivityOutcome,
    TelegramApiError,
    TelegramApiFailure,
    TelegramChat,
    TelegramInlineButton,
    TelegramMessage,
    TelegramResponseUncertain,
    TelegramSendConfirmed,
    TelegramSendOutcome,
    TelegramSendRejected,
    TelegramSendRetry,
    TelegramSendUncertain,
    TelegramUpdates,
    TelegramUser,
    TelegramWebhookCleared,
    TelegramWebhookInfo,
    normalize_command,
    parse_update,
)
from friendly_bot.telegram.poller import (
    TelegramIngress,
    TelegramPoller,
    TelegramPollingGateway,
)
from friendly_bot.telegram.preflight import (
    TelegramPreflight,
    TelegramPreflightError,
    TelegramWebhookGateway,
)
from friendly_bot.telegram.presentations import (
    PresentationBuffer,
    TelegramPhotoPresentation,
    TelegramPresentation,
    TelegramTextPresentation,
)
from friendly_bot.telegram.runtime_lock import (
    TelegramRuntimeAlreadyRunningError,
    TelegramRuntimeLock,
    TelegramRuntimeLockLostError,
)
from friendly_bot.telegram.sender import (
    BestEffortTelegramSender,
    DirectSendResult,
    TelegramPresentationSender,
    TelegramSendGateway,
)


class TelegramActivityGateway(Protocol):
    """The ephemeral Telegram activity boundary used by configured I04 actions."""

    async def send_activity(
        self, *, chat_id: int, activity: str
    ) -> TelegramActivityOutcome:
        """Attempt one short-lived activity indication without durable replay."""


class TelegramGateway(
    TelegramPollingGateway,
    TelegramWebhookGateway,
    TelegramActivityGateway,
    Protocol,
):
    """The complete typed Telegram boundary consumed by I04 composition."""


__all__ = [
    "BestEffortTelegramSender",
    "DirectSendResult",
    "IncomingTelegramUpdate",
    "LocalTelegramAssetResolver",
    "OutboundTelegramMessage",
    "OutboundTelegramPhoto",
    "OutboundTelegramRequest",
    "PresentationBuffer",
    "ResolvedTelegramPhoto",
    "TelegramActivityConfirmed",
    "TelegramActivityGateway",
    "TelegramActivityOutcome",
    "TelegramApiClient",
    "TelegramApiError",
    "TelegramApiFailure",
    "TelegramAssetResolver",
    "TelegramCallback",
    "TelegramCallbackContextKind",
    "TelegramChat",
    "TelegramGateway",
    "TelegramIngress",
    "TelegramInlineButton",
    "TelegramMessage",
    "TelegramPhotoPresentation",
    "TelegramPoller",
    "TelegramPreflight",
    "TelegramPreflightError",
    "TelegramPresentation",
    "TelegramPresentationSender",
    "TelegramResponseUncertain",
    "TelegramRuntimeAlreadyRunningError",
    "TelegramRuntimeLock",
    "TelegramRuntimeLockLostError",
    "TelegramSendConfirmed",
    "TelegramSendGateway",
    "TelegramSendOutcome",
    "TelegramSendRejected",
    "TelegramSendRetry",
    "TelegramSendUncertain",
    "TelegramTextPresentation",
    "TelegramUpdates",
    "TelegramUser",
    "TelegramWebhookCleared",
    "TelegramWebhookGateway",
    "TelegramWebhookInfo",
    "decode_callback",
    "encode_callback",
    "normalize_command",
    "parse_update",
]
