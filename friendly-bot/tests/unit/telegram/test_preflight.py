"""Startup safety contracts for Telegram long polling."""

from __future__ import annotations

import pytest

from friendly_bot.telegram.models import (
    TelegramApiError,
    TelegramApiFailure,
    TelegramResponseUncertain,
    TelegramWebhookCleared,
    TelegramWebhookInfo,
)
from friendly_bot.telegram.preflight import TelegramPreflight, TelegramPreflightError


class FakeWebhookGateway:
    """A minimal Telegram boundary fake that never receives raw webhook URLs."""

    def __init__(
        self,
        webhook_info: TelegramWebhookInfo | TelegramApiFailure,
        clear_result: TelegramWebhookCleared | TelegramApiFailure | None = None,
    ) -> None:
        self._webhook_info = webhook_info
        self._clear_result = (
            TelegramWebhookCleared() if clear_result is None else clear_result
        )
        self.calls: list[tuple[object, ...]] = []

    async def get_webhook_info(self) -> TelegramWebhookInfo | TelegramApiFailure:
        self.calls.append(("getWebhookInfo",))
        return self._webhook_info

    async def clear_webhook(self) -> TelegramWebhookCleared | TelegramApiFailure:
        self.calls.append(("deleteWebhook", {"drop_pending_updates": False}))
        return self._clear_result


async def test_preflight_clears_configured_webhook_without_dropping_updates() -> None:
    """Skipping deletion leaves polling in conflict with Telegram webhook delivery."""

    gateway = FakeWebhookGateway(TelegramWebhookInfo(configured=True))

    await TelegramPreflight(gateway).ensure_polling_ready()

    assert gateway.calls == [
        ("getWebhookInfo",),
        ("deleteWebhook", {"drop_pending_updates": False}),
    ]


async def test_preflight_treats_an_absent_webhook_as_already_ready() -> None:
    """Clearing an absent webhook makes a safe retry perform needless API work."""

    gateway = FakeWebhookGateway(TelegramWebhookInfo(configured=False))

    await TelegramPreflight(gateway).ensure_polling_ready()

    assert gateway.calls == [("getWebhookInfo",)]


@pytest.mark.parametrize(
    "result",
    [TelegramApiError(401), TelegramResponseUncertain("telegram_response_malformed")],
)
async def test_preflight_rejects_untrusted_webhook_state(
    result: TelegramApiFailure,
) -> None:
    """Polling after an unknown webhook result can race an existing webhook owner."""

    gateway = FakeWebhookGateway(result)

    with pytest.raises(TelegramPreflightError, match="^webhook_state_unknown$"):
        await TelegramPreflight(gateway).ensure_polling_ready()

    assert gateway.calls == [("getWebhookInfo",)]


async def test_preflight_rejects_a_webhook_that_could_not_be_cleared() -> None:
    """Starting after a failed deletion would create two Telegram delivery paths."""

    gateway = FakeWebhookGateway(
        TelegramWebhookInfo(configured=True), TelegramApiError(500)
    )

    with pytest.raises(TelegramPreflightError, match="^webhook_clear_failed$"):
        await TelegramPreflight(gateway).ensure_polling_ready()

    assert gateway.calls == [
        ("getWebhookInfo",),
        ("deleteWebhook", {"drop_pending_updates": False}),
    ]
