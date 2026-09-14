"""Terminal logging at the executable application boundary."""

from __future__ import annotations

import logging

import pytest

from friendly_bot import __main__


def test_main_logs_an_unexpected_runtime_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Removing the entry-point logger would leave terminal diagnosis unstructured."""

    async def broken_runtime() -> None:
        raise RuntimeError("test runtime failure")

    monkeypatch.setattr(__main__, "run_application", broken_runtime)

    with pytest.raises(RuntimeError, match="test runtime failure"):
        __main__.main()

    assert caplog.record_tuples == [
        (
            "friendly_bot.__main__",
            logging.ERROR,
            "friendly bot runtime stopped unexpectedly",
        )
    ]
