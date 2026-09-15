"""Parsing helpers shared by publication, reply planning, and local rendering."""

from __future__ import annotations

import re

TEMPLATE_TOKEN_PATTERN = re.compile(
    r"\{\{\s*([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)"
    r"\s*(?:\|\s*optional)?\s*\}\}"
)
URL_PATTERN = re.compile(r"https?://[^\s<>{}]+")


def template_tokens(text: str) -> tuple[str, ...]:
    """Return normalized template tokens in source order, including duplicates."""

    if not isinstance(text, str):
        raise TypeError("template text must be a string")
    return tuple(match.group(1) for match in TEMPLATE_TOKEN_PATTERN.finditer(text))


def urls(text: str) -> tuple[str, ...]:
    """Return literal HTTP and HTTPS URLs in source order."""

    if not isinstance(text, str):
        raise TypeError("URL source text must be a string")
    return tuple(match.group(0) for match in URL_PATTERN.finditer(text))
