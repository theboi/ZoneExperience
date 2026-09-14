"""Fail-closed local catalog resolver for durable Telegram photo keys."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol, cast

from friendly_bot.telegram.models import ResolvedTelegramPhoto


class TelegramAssetResolver(Protocol):
    """Resolve only a stable key to verified local bytes at the send boundary."""

    def resolve_photo(self, asset_key: str) -> ResolvedTelegramPhoto | None:
        """Return a verified photo, or no result for every invalid local condition."""


class LocalTelegramAssetResolver:
    """Resolve catalogued PNG/JPEG files without exposing their local source paths."""

    def __init__(self, asset_root: Path, catalog_path: Path) -> None:
        self._asset_root = asset_root
        self._catalog_path = catalog_path

    def resolve_photo(self, asset_key: str) -> ResolvedTelegramPhoto | None:
        if type(asset_key) is not str or not asset_key:
            return None
        entry = self._entry(asset_key)
        if entry is None:
            return None
        path, media_type, expected_hash = entry
        resolved = self._resolved_file(path)
        if resolved is None:
            return None
        try:
            content = resolved.read_bytes()
        except OSError:
            return None
        if hashlib.sha256(content).hexdigest() != expected_hash:
            return None
        if _media_type_from_content(content) != media_type:
            return None
        try:
            return ResolvedTelegramPhoto(resolved.name, media_type, content)
        except ValueError:
            return None

    def _entry(
        self, asset_key: str
    ) -> tuple[Path, Literal["image/png", "image/jpeg"], str] | None:
        try:
            parsed = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, Mapping):
            return None
        entry = parsed.get(asset_key)
        if not isinstance(entry, Mapping) or set(entry) != {
            "path",
            "media_type",
            "sha256",
        }:
            return None
        path = entry.get("path")
        media_type = entry.get("media_type")
        expected_hash = entry.get("sha256")
        if (
            type(path) is not str
            or type(media_type) is not str
            or type(expected_hash) is not str
            or len(expected_hash) != 64
            or any(character not in _HEX for character in expected_hash)
            or media_type not in {"image/png", "image/jpeg"}
        ):
            return None
        relative_path = Path(path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            return None
        return (
            relative_path,
            cast(Literal["image/png", "image/jpeg"], media_type),
            expected_hash,
        )

    def _resolved_file(self, relative_path: Path) -> Path | None:
        try:
            root = self._asset_root.resolve(strict=True)
            candidate = (root / relative_path).resolve(strict=True)
        except OSError:
            return None
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return None
        return candidate


def _media_type_from_content(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


_HEX = frozenset("0123456789abcdef")
