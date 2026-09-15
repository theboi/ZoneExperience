"""Best-effort direct delivery of already-committed Telegram presentations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

import httpx

from friendly_bot.telegram.assets import TelegramAssetResolver
from friendly_bot.telegram.models import (
    OutboundTelegramMessage,
    OutboundTelegramPhoto,
    OutboundTelegramRequest,
    TelegramResponseUncertain,
    TelegramSendConfirmed,
    TelegramSendOutcome,
    TelegramSendRejected,
    TelegramSendRetry,
)
from friendly_bot.telegram.presentations import (
    TelegramPhotoPresentation,
    TelegramPresentation,
    TelegramTextPresentation,
)


class TelegramSendGateway(Protocol):
    """The minimal direct send operation needed after database commit."""

    async def send(self, request: OutboundTelegramRequest) -> TelegramSendOutcome:
        """Attempt one Telegram request."""


class DirectSendLogger(Protocol):
    """Record only stable, privacy-safe direct-send outcomes."""

    def info(self, event: str) -> None:
        """Record one stable event name."""


class TelegramPresentationSender(Protocol):
    """Send a frozen presentation batch after its originating transaction commits."""

    async def send_all(
        self, presentations: tuple[TelegramPresentation, ...]
    ) -> DirectSendResult:
        """Attempt every presentation once in source order."""


@dataclass(frozen=True, slots=True)
class DirectSendResult:
    """Aggregate the bounded one-attempt delivery outcome without message content."""

    attempted: int
    confirmed: int
    failed: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.attempted, self.confirmed, self.failed)
        ):
            raise ValueError("Direct send counts must be nonnegative integers")
        if self.attempted != self.confirmed + self.failed:
            raise ValueError("Direct send totals must equal attempted sends")


class BestEffortTelegramSender:
    """Send each presentation once, never retry, and contain ordinary failures."""

    def __init__(
        self,
        gateway: TelegramSendGateway,
        *,
        asset_resolver: TelegramAssetResolver | None = None,
        logger: DirectSendLogger | None = None,
    ) -> None:
        self._gateway = gateway
        self._asset_resolver = asset_resolver
        self._logger = logger

    async def send_all(
        self, presentations: tuple[TelegramPresentation, ...]
    ) -> DirectSendResult:
        """Attempt each immutable presentation once, preserving its order."""

        attempted = 0
        confirmed = 0
        failed = 0
        for presentation in presentations:
            attempted += 1
            outcome = await self._send_one(presentation)
            self._record(outcome)
            if outcome == "confirmed":
                confirmed += 1
            else:
                failed += 1
        return DirectSendResult(attempted, confirmed, failed)

    async def _send_one(self, presentation: TelegramPresentation) -> str:
        request = self._request_for(presentation)
        if request is None:
            return "asset_unavailable"
        try:
            outcome = await self._gateway.send(request)
        except asyncio.CancelledError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            return "transport_error"
        except Exception:  # noqa: BLE001
            return "unexpected_error"
        return _outcome_code(outcome)

    def _request_for(
        self, presentation: TelegramPresentation
    ) -> OutboundTelegramRequest | None:
        correlation_key = f"direct:{uuid4()}"
        if isinstance(presentation, TelegramTextPresentation):
            return OutboundTelegramMessage(
                presentation.chat_id,
                presentation.text,
                correlation_key,
                presentation.buttons,
            )
        if not isinstance(presentation, TelegramPhotoPresentation):
            return None
        if self._asset_resolver is None:
            return None
        photo = self._asset_resolver.resolve_photo(presentation.asset_key)
        if photo is None:
            return None
        return OutboundTelegramPhoto(
            presentation.chat_id,
            photo,
            presentation.caption,
            correlation_key,
            presentation.buttons,
        )

    def _record(self, outcome: str) -> None:
        if self._logger is not None:
            self._logger.info(f"telegram_direct_send outcome={outcome}")


def _outcome_code(outcome: TelegramSendOutcome) -> str:
    if isinstance(outcome, TelegramSendConfirmed):
        return "confirmed"
    if isinstance(outcome, TelegramSendRetry):
        return "retry"
    if isinstance(outcome, TelegramSendRejected):
        return "rejected"
    if isinstance(outcome, TelegramResponseUncertain):
        return "uncertain"
    return "unexpected_outcome"
