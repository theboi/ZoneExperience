"""Local Friendly Bot process entry point."""

from __future__ import annotations

import asyncio

from friendly_bot.app import run_application


def main() -> None:
    """Run the application only when this module is explicitly invoked."""

    asyncio.run(run_application())


if __name__ == "__main__":
    main()
