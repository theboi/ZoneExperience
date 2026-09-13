"""Direct, non-retrying and body-redacting Telegram Bot API transport."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Final, TypeGuard

import httpx
from pydantic import SecretStr

from friendly_bot.telegram.models import (
    OutboundTelegramMessage,
    TelegramApiError,
    TelegramApiFailure,
    TelegramResponseUncertain,
    TelegramSendConfirmed,
    TelegramSendOutcome,
    TelegramSendRejected,
    TelegramSendRetry,
    TelegramUpdates,
    TelegramWebhookCleared,
    TelegramWebhookInfo,
    parse_update,
)

_LONG_POLL_READ_MARGIN_SECONDS: Final = 5.0


class TelegramApiClient:
    """One direct Bot API client that retains only typed minimal outcomes."""

    def __init__(
        self,
        bot_token: SecretStr,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._bot_token = bot_token
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close only the HTTP client owned by this Telegram transport."""

        if self._owns_client:
            await self._client.aclose()

    async def get_webhook_info(
        self,
    ) -> TelegramWebhookInfo | TelegramApiFailure:
        """Return only whether a webhook is configured, never its URL."""

        envelope = _parse_envelope(await self._get("getWebhookInfo"))
        if isinstance(envelope, (TelegramApiError, TelegramResponseUncertain)):
            return envelope
        result = envelope.get("result")
        if not isinstance(result, dict):
            return _malformed()
        url = result.get("url")
        if type(url) is not str:
            return _malformed()
        return TelegramWebhookInfo(configured=bool(url))

    async def clear_webhook(self) -> TelegramWebhookCleared | TelegramApiFailure:
        """Clear a webhook while explicitly preserving pending Telegram updates."""

        envelope = _parse_envelope(
            await self._post("deleteWebhook", {"drop_pending_updates": False})
        )
        if isinstance(envelope, (TelegramApiError, TelegramResponseUncertain)):
            return envelope
        if envelope.get("result") is not True:
            return _malformed()
        return TelegramWebhookCleared()

    async def get_updates(
        self, *, offset: int, timeout_seconds: int
    ) -> TelegramUpdates | TelegramApiFailure:
        """Fetch one strict update batch for the caller-owned durable offset."""

        if not _nonnegative_int(offset):
            raise ValueError("Telegram update offset must be nonnegative")
        if not _positive_int(timeout_seconds):
            raise ValueError("Telegram long-poll timeout must be positive")
        envelope = _parse_envelope(
            await self._get(
                "getUpdates",
                params={
                    "offset": str(offset),
                    "timeout": str(timeout_seconds),
                    "allowed_updates": json.dumps(
                        ["message", "callback_query"], separators=(",", ":")
                    ),
                },
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=timeout_seconds + _LONG_POLL_READ_MARGIN_SECONDS,
                    write=10.0,
                    pool=5.0,
                ),
            )
        )
        if isinstance(envelope, (TelegramApiError, TelegramResponseUncertain)):
            return envelope
        raw_updates = envelope.get("result")
        if not isinstance(raw_updates, list):
            return _malformed()
        updates = []
        for raw_update in raw_updates:
            update = parse_update(raw_update)
            if isinstance(update, TelegramResponseUncertain):
                return update
            updates.append(update)
        return TelegramUpdates(tuple(updates))

    async def send(self, request: OutboundTelegramMessage) -> TelegramSendOutcome:
        """Send an immutable message once, without generic retry behavior or logging."""

        response = await self._post(
            "sendMessage", {"chat_id": request.chat_id, "text": request.text}
        )
        envelope = _parse_envelope(response)
        if isinstance(envelope, TelegramResponseUncertain):
            return envelope
        if isinstance(envelope, TelegramApiError):
            if envelope.error_code == 429:
                retry_after_seconds = _retry_after(response)
                if retry_after_seconds is None:
                    return _malformed()
                return TelegramSendRetry(429, retry_after_seconds)
            if 500 <= envelope.error_code <= 599:
                return TelegramSendRetry(envelope.error_code)
            return TelegramSendRejected(envelope.error_code)
        result = envelope.get("result")
        if not isinstance(result, dict):
            return _malformed()
        message_id = result.get("message_id")
        if not _positive_int(message_id):
            return _malformed()
        return TelegramSendConfirmed(message_id)

    def _endpoint(self, method: str) -> str:
        return (
            f"https://api.telegram.org/bot{self._bot_token.get_secret_value()}/{method}"
        )

    async def _get(
        self,
        method: str,
        *,
        params: Mapping[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response | TelegramResponseUncertain:
        try:
            return await self._client.get(
                self._endpoint(method), params=params, timeout=timeout
            )
        except (httpx.TimeoutException, httpx.TransportError):
            return TelegramResponseUncertain("telegram_transport_error")

    async def _post(
        self, method: str, payload: Mapping[str, object]
    ) -> httpx.Response | TelegramResponseUncertain:
        try:
            return await self._client.post(self._endpoint(method), json=payload)
        except (asyncio.CancelledError, httpx.TimeoutException, httpx.TransportError):
            return TelegramResponseUncertain("telegram_transport_error")


def _parse_envelope(
    response: httpx.Response | TelegramResponseUncertain,
) -> dict[str, object] | TelegramApiError | TelegramResponseUncertain:
    if isinstance(response, TelegramResponseUncertain):
        return response
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return _malformed()
    if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
        return _malformed()
    if payload["ok"]:
        if response.status_code != 200:
            return _malformed()
        return payload
    error_code = payload.get("error_code")
    if (
        not _positive_int(error_code)
        or not 400 <= error_code <= 599
        or response.status_code != error_code
    ):
        return _malformed()
    return TelegramApiError(error_code)


def _retry_after(
    response: httpx.Response | TelegramResponseUncertain,
) -> int | None:
    if isinstance(response, TelegramResponseUncertain):
        return None
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        return None
    retry_after_seconds = parameters.get("retry_after")
    return retry_after_seconds if _positive_int(retry_after_seconds) else None


def _malformed() -> TelegramResponseUncertain:
    return TelegramResponseUncertain("telegram_response_malformed")


def _positive_int(value: object) -> TypeGuard[int]:
    return type(value) is int and value > 0


def _nonnegative_int(value: object) -> TypeGuard[int]:
    return type(value) is int and value >= 0
