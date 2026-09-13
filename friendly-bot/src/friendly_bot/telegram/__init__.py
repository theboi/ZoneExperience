"""Strict Telegram Bot API boundary for Friendly Bot."""

from friendly_bot.telegram.client import TelegramApiClient
from friendly_bot.telegram.models import (
    IncomingTelegramUpdate,
    OutboundTelegramMessage,
    TelegramApiError,
    TelegramApiFailure,
    TelegramChat,
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
from friendly_bot.telegram.preflight import TelegramPreflight, TelegramPreflightError
from friendly_bot.telegram.runtime_lock import (
    TelegramRuntimeAlreadyRunningError,
    TelegramRuntimeLock,
    TelegramRuntimeLockLostError,
)

__all__ = [
    "IncomingTelegramUpdate",
    "OutboundTelegramMessage",
    "TelegramApiClient",
    "TelegramApiError",
    "TelegramApiFailure",
    "TelegramChat",
    "TelegramMessage",
    "TelegramPreflight",
    "TelegramPreflightError",
    "TelegramResponseUncertain",
    "TelegramRuntimeAlreadyRunningError",
    "TelegramRuntimeLock",
    "TelegramRuntimeLockLostError",
    "TelegramSendConfirmed",
    "TelegramSendOutcome",
    "TelegramSendRejected",
    "TelegramSendRetry",
    "TelegramSendUncertain",
    "TelegramUpdates",
    "TelegramUser",
    "TelegramWebhookCleared",
    "TelegramWebhookInfo",
    "normalize_command",
    "parse_update",
]
