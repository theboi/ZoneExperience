"""Fail-closed webhook preflight before Friendly Bot begins long polling."""

from __future__ import annotations

from typing import Protocol

from friendly_bot.telegram.models import (
    TelegramApiFailure,
    TelegramWebhookCleared,
    TelegramWebhookInfo,
)


class TelegramPreflightError(RuntimeError):
    """Telegram webhook state prevents a safe long-polling startup."""


class TelegramWebhookGateway(Protocol):
    """The webhook subset of Task 1's typed Telegram API client."""

    async def get_webhook_info(self) -> TelegramWebhookInfo | TelegramApiFailure:
        """Return the minimal webhook state without exposing its URL."""

    async def clear_webhook(self) -> TelegramWebhookCleared | TelegramApiFailure:
        """Clear a configured webhook while preserving pending updates."""


class TelegramPreflight:
    """Ensure Telegram webhook delivery cannot race this process's long polling."""

    def __init__(self, gateway: TelegramWebhookGateway) -> None:
        self._gateway = gateway

    async def ensure_polling_ready(self) -> None:
        """Clear a configured webhook, or stop startup when its state is untrusted."""

        webhook_info = await self._gateway.get_webhook_info()
        if not isinstance(webhook_info, TelegramWebhookInfo):
            raise TelegramPreflightError("webhook_state_unknown")
        if not webhook_info.configured:
            return

        cleared = await self._gateway.clear_webhook()
        if not isinstance(cleared, TelegramWebhookCleared):
            raise TelegramPreflightError("webhook_clear_failed")
