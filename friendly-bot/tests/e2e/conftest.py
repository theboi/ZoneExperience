"""Shared fixed-time fixtures for I04 end-to-end composition tests."""

from datetime import UTC, datetime

import pytest


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
