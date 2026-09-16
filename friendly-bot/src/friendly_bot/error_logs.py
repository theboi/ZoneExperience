"""Local, ignored error logs for operator investigation."""

from __future__ import annotations

import json
import logging
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from friendly_bot.config.settings import PROJECT_ROOT

LOGGER = logging.getLogger(__name__)
ERROR_LOG_DIRECTORY = PROJECT_ROOT / ".runtime" / "error-logs"


def write_error_log(
    error: BaseException | None,
    *,
    summary: str,
    context: dict[str, Any] | None = None,
    directory: Path = ERROR_LOG_DIRECTORY,
) -> Path | None:
    """Persist one private traceback file and return its path without raising."""

    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="friendly-bot-error-",
            suffix=".log",
            dir=directory,
            delete=False,
        ) as file:
            file.write("Friendly Bot error log\n")
            file.write(
                f"Created at: {datetime.now(UTC).isoformat()}\nSummary: {summary}\n"
            )
            if context:
                file.write(
                    "Context:\n"
                    f"{json.dumps(context, ensure_ascii=False, indent=2, default=str)}\n"
                )
            file.write("Exception:\n")
            if error is None:
                file.write("<unavailable>\n")
            else:
                file.writelines(traceback.format_exception(error))
            path = Path(file.name)
        path.chmod(0o600)
        return path
    except OSError:
        LOGGER.exception("could not write Friendly Bot error log")
        return None


def error_log_name(path: Path | None) -> str:
    """Return only the local log filename safe to show in an error response."""

    if path is None:
        return "unavailable"
    return path.name
