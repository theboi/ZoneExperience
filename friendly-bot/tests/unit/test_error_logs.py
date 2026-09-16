"""Coverage for local, ignored operator error logs."""

from __future__ import annotations

import stat
from pathlib import Path

from friendly_bot.error_logs import write_error_log


def test_write_error_log_records_context_traceback_and_private_permissions(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "error-logs"
    try:
        raise RuntimeError("test failure")
    except RuntimeError as error:
        path = write_error_log(
            error,
            summary="test error",
            context={"reason_code": "test.failure"},
            directory=directory,
        )

    assert path is not None
    assert path.parent == directory
    assert path.suffix == ".log"
    contents = path.read_text(encoding="utf-8")
    assert "Friendly Bot error log" in contents
    assert "Summary: test error" in contents
    assert '"reason_code": "test.failure"' in contents
    assert "RuntimeError: test failure" in contents
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
