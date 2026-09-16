"""Local Friendly Bot process entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from friendly_bot.app import run_application

LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] = ()) -> None:
    """Run the application only when this module is explicitly invoked."""

    parser = argparse.ArgumentParser(description="Run the Friendly Bot runtime.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="print OpenRouter request and response bodies to this terminal",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if arguments.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if arguments.debug:
        LOGGER.warning(
            "debug mode is enabled; OpenRouter inputs and outputs will be printed to this terminal"
        )
    try:
        runtime = run_application(debug=True) if arguments.debug else run_application()
        asyncio.run(runtime)
    except KeyboardInterrupt:
        LOGGER.info("friendly bot runtime stopped")
    except Exception:
        LOGGER.exception("friendly bot runtime stopped unexpectedly")
        raise


if __name__ == "__main__":
    main(sys.argv[1:])
