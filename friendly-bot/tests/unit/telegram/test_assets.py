"""Integrity and path-confinement tests for Telegram photo assets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from friendly_bot.telegram.assets import LocalTelegramAssetResolver

_PNG = b"\x89PNG\r\n\x1a\nvalid-image"


def _catalog(
    root: Path, *, digest: str | None = None, path: str = "poster.png"
) -> Path:
    catalog = root / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "zone_x_poster_2026": {
                    "path": path,
                    "media_type": "image/png",
                    "sha256": digest or hashlib.sha256(_PNG).hexdigest(),
                }
            }
        ),
        encoding="utf-8",
    )
    return catalog


def test_resolver_returns_only_catalogued_matching_local_png(tmp_path: Path) -> None:
    (tmp_path / "poster.png").write_bytes(_PNG)
    resolver = LocalTelegramAssetResolver(tmp_path, _catalog(tmp_path))

    resolved = resolver.resolve_photo("zone_x_poster_2026")

    assert resolved is not None
    assert resolved.filename == "poster.png"
    assert resolved.media_type == "image/png"
    assert resolved.content == _PNG


def test_resolver_rejects_unknown_escape_or_hash_mismatch(tmp_path: Path) -> None:
    (tmp_path / "poster.png").write_bytes(_PNG)
    resolver = LocalTelegramAssetResolver(
        tmp_path,
        _catalog(tmp_path, digest="0" * 64),
    )
    escaped = LocalTelegramAssetResolver(
        tmp_path, _catalog(tmp_path, path="../poster.png")
    )

    assert resolver.resolve_photo("zone_x_poster_2026") is None
    assert resolver.resolve_photo("unknown") is None
    assert escaped.resolve_photo("zone_x_poster_2026") is None


def test_zone_x_catalog_resolves_the_committed_development_poster() -> None:
    root = Path(__file__).parents[3] / "assets"
    resolver = LocalTelegramAssetResolver(root, root / "catalog.json")

    resolved = resolver.resolve_photo("zone_x_poster_2026")

    assert resolved is not None
    assert resolved.filename == "zone_x_poster_2026.png"
    assert resolved.media_type == "image/png"
