"""Immutable Telegram presentations accumulated before a post-commit send."""

from __future__ import annotations

from dataclasses import dataclass, field

from friendly_bot.telegram.models import TelegramInlineButton


@dataclass(frozen=True, slots=True)
class TelegramTextPresentation:
    """One fully rendered Telegram text message awaiting a best-effort send."""

    chat_id: int
    text: str
    buttons: tuple[TelegramInlineButton, ...] = ()

    def __post_init__(self) -> None:
        _validate_chat_id(self.chat_id)
        if type(self.text) is not str or not self.text:
            raise ValueError("Telegram presentation text must be nonempty")
        _validate_buttons(self.buttons)


@dataclass(frozen=True, slots=True)
class TelegramPhotoPresentation:
    """One asset-keyed Telegram photo message awaiting a best-effort send."""

    chat_id: int
    asset_key: str
    caption: str
    buttons: tuple[TelegramInlineButton, ...] = ()

    def __post_init__(self) -> None:
        _validate_chat_id(self.chat_id)
        if type(self.asset_key) is not str or not self.asset_key:
            raise ValueError("Telegram presentation asset key must be nonempty")
        if type(self.caption) is not str or not self.caption:
            raise ValueError("Telegram presentation caption must be nonempty")
        _validate_buttons(self.buttons)


type TelegramPresentation = TelegramTextPresentation | TelegramPhotoPresentation


@dataclass(slots=True)
class PresentationBuffer:
    """Preserve presentation order inside one caller-owned transaction scope."""

    presentations: list[TelegramPresentation] = field(default_factory=list)

    def append(self, presentation: TelegramPresentation) -> None:
        """Append exactly one typed presentation in execution order."""

        if not isinstance(
            presentation, (TelegramTextPresentation, TelegramPhotoPresentation)
        ):
            raise TypeError("Telegram presentation must be typed")
        self.presentations.append(presentation)

    def snapshot(self) -> tuple[TelegramPresentation, ...]:
        """Return an immutable ordered view for post-commit delivery."""

        return tuple(self.presentations)


def _validate_chat_id(chat_id: int) -> None:
    if type(chat_id) is not int or chat_id <= 0:
        raise ValueError("Telegram presentation chat id must be positive")


def _validate_buttons(buttons: tuple[TelegramInlineButton, ...]) -> None:
    if type(buttons) is not tuple or not all(
        isinstance(button, TelegramInlineButton) for button in buttons
    ):
        raise ValueError("Telegram presentation buttons must be an immutable typed tuple")
