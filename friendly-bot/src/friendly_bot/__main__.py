"""Local Friendly Bot process entry point."""

from __future__ import annotations

import asyncio
import logging

from friendly_bot.app import run_application

LOGGER = logging.getLogger(__name__)


def main() -> None:
    """Run the application only when this module is explicitly invoked."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run_application())
    except KeyboardInterrupt:
        LOGGER.info("friendly bot runtime stopped")
    except Exception:
        LOGGER.exception("friendly bot runtime stopped unexpectedly")
        raise


if __name__ == "__main__":
    main()
