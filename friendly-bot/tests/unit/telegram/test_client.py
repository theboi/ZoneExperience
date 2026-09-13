"""Behavioral contracts for the direct, strict Telegram Bot API client."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from friendly_bot.telegram.client import TelegramApiClient
from friendly_bot.telegram.models import (
    OutboundTelegramMessage,
    TelegramApiError,
    TelegramResponseUncertain,
    TelegramSendConfirmed,
    TelegramSendRejected,
    TelegramSendRetry,
    TelegramUpdates,
    TelegramWebhookCleared,
    TelegramWebhookInfo,
)


def _client(handler: httpx.AsyncBaseTransport) -> TelegramApiClient:
    return TelegramApiClient(
        SecretStr("test-token"), client=httpx.AsyncClient(transport=handler)
    )


def _outbound() -> OutboundTelegramMessage:
    return OutboundTelegramMessage(
        chat_id=42, text="Plain text", idempotency_key="delivery-1"
    )


async def test_send_confirms_only_a_positive_message_identifier() -> None:
    """A valid receipt must retain only the delivery identifier needed by the outbox."""

    async def telegram(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bottest-token/sendMessage"
        assert json.loads(request.content) == {"chat_id": 42, "text": "Plain text"}
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 17}})

    client = _client(httpx.MockTransport(telegram))

    assert await client.send(_outbound()) == TelegramSendConfirmed(17)
    await client.aclose()


async def test_send_uses_a_positive_429_retry_after_as_a_definite_retry() -> None:
    """A complete flood-control receipt proves the send did not occur and gives its delay."""

    client = _client(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "parameters": {"retry_after": 12},
                },
            )
        )
    )

    assert await client.send(_outbound()) == TelegramSendRetry(429, 12)
    await client.aclose()


async def test_send_retries_only_a_parsed_telegram_5xx() -> None:
    """Retrying a proxy error could duplicate a message whose delivery is unknown."""

    client = _client(
        httpx.MockTransport(
            lambda _request: httpx.Response(503, json={"ok": False, "error_code": 503})
        )
    )

    assert await client.send(_outbound()) == TelegramSendRetry(503)
    await client.aclose()


async def test_send_rejects_a_parsed_permanent_4xx() -> None:
    """A definite permanent Telegram rejection must not consume a retry slot."""

    client = _client(
        httpx.MockTransport(
            lambda _request: httpx.Response(403, json={"ok": False, "error_code": 403})
        )
    )

    assert await client.send(_outbound()) == TelegramSendRejected(403)
    await client.aclose()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"ok": True, "result": {"message_id": 0}}),
        httpx.Response(200, json={"ok": False, "error_code": 429}),
        httpx.Response(503, json={"ok": True, "result": {"message_id": 17}}),
        httpx.Response(503, json={"ok": False, "error_code": 400}),
    ],
)
async def test_send_marks_malformed_or_impossible_responses_uncertain(
    response: httpx.Response,
) -> None:
    """A response that cannot prove a send outcome must be held for review, not replayed."""

    client = _client(httpx.MockTransport(lambda _request: response))

    assert await client.send(_outbound()) == TelegramResponseUncertain(
        "telegram_response_malformed"
    )
    await client.aclose()


async def test_send_marks_a_transport_failure_uncertain() -> None:
    """A connection failure after starting a send cannot prove that Telegram did not deliver."""

    client = _client(
        httpx.MockTransport(
            lambda _request: (_ for _ in ()).throw(httpx.ConnectError("offline"))
        )
    )

    assert await client.send(_outbound()) == TelegramResponseUncertain(
        "telegram_transport_error"
    )
    await client.aclose()


async def test_get_updates_requests_both_supported_update_kinds_with_a_read_margin() -> (
    None
):
    """Ignoring callbacks or expiring exactly at Telegram's hold limit loses interaction input."""

    seen_read_timeout: float | None = None

    async def telegram(request: httpx.Request) -> httpx.Response:
        nonlocal seen_read_timeout
        seen_read_timeout = request.extensions["timeout"]["read"]
        assert dict(request.url.params) == {
            "offset": "8",
            "timeout": "30",
            "allowed_updates": '["message","callback_query"]',
        }
        return httpx.Response(200, json={"ok": True, "result": []})

    client = _client(httpx.MockTransport(telegram))

    assert await client.get_updates(offset=8, timeout_seconds=30) == TelegramUpdates(())
    assert seen_read_timeout is not None
    assert seen_read_timeout > 30
    await client.aclose()


async def test_webhook_methods_keep_only_configured_state_and_clear_without_dropping_updates() -> (
    None
):
    """Preflight needs state and successful clearing, but must not retain a webhook URL."""

    seen: list[tuple[str, object]] = []

    async def telegram(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("getWebhookInfo"):
            seen.append(("getWebhookInfo", None))
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {"url": "https://example.test/secret-hook"},
                },
            )
        seen.append(("deleteWebhook", json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": True})

    client = _client(httpx.MockTransport(telegram))

    info = await client.get_webhook_info()
    cleared = await client.clear_webhook()

    assert info == TelegramWebhookInfo(configured=True)
    assert not hasattr(info, "url")
    assert cleared == TelegramWebhookCleared()
    assert seen == [
        ("getWebhookInfo", None),
        ("deleteWebhook", {"drop_pending_updates": False}),
    ]
    await client.aclose()


async def test_api_calls_mark_a_malformed_update_batch_uncertain() -> None:
    """Partially accepting an invalid batch could incorrectly advance the durable cursor."""

    client = _client(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"ok": True, "result": [{"update_id": 1, "message": []}]},
            )
        )
    )

    assert await client.get_updates(
        offset=0, timeout_seconds=1
    ) == TelegramResponseUncertain("telegram_response_malformed")
    await client.aclose()


async def test_api_calls_return_a_typed_definite_error_without_response_body() -> None:
    """Preflight must receive only a stable API error code, never Telegram's raw response."""

    client = _client(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                401, json={"ok": False, "error_code": 401, "description": "secret"}
            )
        )
    )

    result = await client.get_webhook_info()

    assert result == TelegramApiError(401)
    assert not hasattr(result, "description")
    await client.aclose()
