"""Strict Telegram Bot API boundary for Friendly Bot."""

from typing import Protocol

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
from friendly_bot.telegram.outbox import OutboundDeliveryWorker, TelegramDeliveryGateway
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
from friendly_bot.telegram.runtime_lock import (
    TelegramRuntimeAlreadyRunningError,
    TelegramRuntimeLock,
    TelegramRuntimeLockLostError,
)


class TelegramGateway(
    TelegramPollingGateway,
    TelegramDeliveryGateway,
    TelegramWebhookGateway,
    Protocol,
):
    """The complete typed Telegram boundary consumed by I04 composition."""


__all__ = [
    "IncomingTelegramUpdate",
    "OutboundDeliveryWorker",
    "OutboundTelegramMessage",
    "TelegramApiClient",
    "TelegramApiError",
    "TelegramApiFailure",
    "TelegramChat",
    "TelegramGateway",
    "TelegramIngress",
    "TelegramMessage",
    "TelegramPoller",
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
    "TelegramWebhookGateway",
    "TelegramWebhookInfo",
    "normalize_command",
    "parse_update",
]
