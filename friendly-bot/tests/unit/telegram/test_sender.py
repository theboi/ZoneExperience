"""Contracts for best-effort, post-commit Telegram presentations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx
import pytest

from friendly_bot.telegram.models import (
    OutboundTelegramMessage,
    OutboundTelegramPhoto,
    OutboundTelegramRequest,
    ResolvedTelegramPhoto,
    TelegramInlineButton,
    TelegramResponseUncertain,
    TelegramSendConfirmed,
    TelegramSendOutcome,
    TelegramSendRejected,
    TelegramSendRetry,
)
from friendly_bot.telegram.presentations import (
    PresentationBuffer,
    TelegramPhotoPresentation,
    TelegramTextPresentation,
)
from friendly_bot.telegram.sender import BestEffortTelegramSender, DirectSendResult


@dataclass
class RecordingGateway:
    """Gateway double that retains only the outgoing typed request."""

    outcomes: list[TelegramSendOutcome | BaseException]
    requests: list[OutboundTelegramRequest] = field(default_factory=list)

    async def send(self, request: OutboundTelegramRequest) -> TelegramSendOutcome:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@dataclass(frozen=True)
class StaticAssetResolver:
    photo: ResolvedTelegramPhoto | None

    def resolve_photo(self, asset_key: str) -> ResolvedTelegramPhoto | None:
        assert asset_key == "zone_x_poster"
        return self.photo


@dataclass
class RecordingLogger:
    events: list[str] = field(default_factory=list)

    def info(self, event: str) -> None:
        self.events.append(event)


def test_presentations_are_immutable_and_buffer_in_order() -> None:
    button = TelegramInlineButton("Continue", "zone_x.menu.connect")
    first = TelegramTextPresentation(73, "hello", (button,))
    second = TelegramPhotoPresentation(73, "zone_x_poster", "come along")
    buffer = PresentationBuffer()

    buffer.append(first)
    buffer.append(second)

    assert buffer.snapshot() == (first, second)
    with pytest.raises(AttributeError):
        first.text = "changed"  # type: ignore[misc]


async def test_sender_attempts_each_ordered_presentation_once() -> None:
    gateway = RecordingGateway([TelegramSendConfirmed(1), TelegramSendConfirmed(2)])
    logger = RecordingLogger()
    sender = BestEffortTelegramSender(gateway, logger=logger)

    result = await sender.send_all(
        (
            TelegramTextPresentation(chat_id=73, text="hello", buttons=()),
            TelegramTextPresentation(chat_id=73, text="again", buttons=()),
        )
    )

    assert result == DirectSendResult(attempted=2, confirmed=2, failed=0)
    assert [
        request.text
        for request in gateway.requests
        if isinstance(request, OutboundTelegramMessage)
    ] == ["hello", "again"]
    assert logger.events == [
        "telegram_direct_send outcome=confirmed",
        "telegram_direct_send outcome=confirmed",
    ]


async def test_sender_resolves_photo_immediately_before_its_one_attempt() -> None:
    photo = ResolvedTelegramPhoto("zone_x_poster.png", "image/png", b"png")
    gateway = RecordingGateway([TelegramSendConfirmed(11)])
    sender = BestEffortTelegramSender(
        gateway,
        asset_resolver=StaticAssetResolver(photo),
    )

    result = await sender.send_all(
        (TelegramPhotoPresentation(73, "zone_x_poster", "come along"),)
    )

    assert result == DirectSendResult(attempted=1, confirmed=1, failed=0)
    request = gateway.requests[0]
    assert isinstance(request, OutboundTelegramPhoto)
    assert request.photo == photo


@pytest.mark.parametrize(
    ("outcome", "expected_event"),
    [
        (TelegramSendRetry(503), "telegram_direct_send outcome=retry"),
        (TelegramSendRejected(403), "telegram_direct_send outcome=rejected"),
        (
            TelegramResponseUncertain("telegram_response_malformed"),
            "telegram_direct_send outcome=uncertain",
        ),
        (httpx.ConnectError("down"), "telegram_direct_send outcome=transport_error"),
    ],
)
async def test_sender_contains_ordinary_failures_and_continues(
    outcome: TelegramSendOutcome | BaseException, expected_event: str
) -> None:
    gateway = RecordingGateway([outcome, TelegramSendConfirmed(2)])
    logger = RecordingLogger()
    sender = BestEffortTelegramSender(gateway, logger=logger)

    result = await sender.send_all(
        (
            TelegramTextPresentation(73, "first"),
            TelegramTextPresentation(73, "second"),
        )
    )

    assert result == DirectSendResult(attempted=2, confirmed=1, failed=1)
    assert [
        request.text
        for request in gateway.requests
        if isinstance(request, OutboundTelegramMessage)
    ] == ["first", "second"]
    assert logger.events == [expected_event, "telegram_direct_send outcome=confirmed"]


async def test_sender_counts_an_unresolved_photo_as_one_failed_attempt() -> None:
    gateway = RecordingGateway([])
    logger = RecordingLogger()
    sender = BestEffortTelegramSender(
        gateway,
        asset_resolver=StaticAssetResolver(None),
        logger=logger,
    )

    result = await sender.send_all(
        (TelegramPhotoPresentation(73, "zone_x_poster", "come along"),)
    )

    assert result == DirectSendResult(attempted=1, confirmed=0, failed=1)
    assert gateway.requests == []
    assert logger.events == ["telegram_direct_send outcome=asset_unavailable"]


async def test_sender_propagates_cancellation_without_a_second_attempt() -> None:
    gateway = RecordingGateway([asyncio.CancelledError(), TelegramSendConfirmed(2)])
    sender = BestEffortTelegramSender(gateway)

    with pytest.raises(asyncio.CancelledError):
        await sender.send_all(
            (
                TelegramTextPresentation(73, "first"),
                TelegramTextPresentation(73, "second"),
            )
        )

    assert [
        request.text
        for request in gateway.requests
        if isinstance(request, OutboundTelegramMessage)
    ] == ["first"]
