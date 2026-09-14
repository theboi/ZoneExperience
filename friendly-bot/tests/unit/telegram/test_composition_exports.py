"""Public T02 imports consumed by I04 runtime composition."""

from __future__ import annotations

from importlib import import_module


def test_i04_can_import_the_documented_t02_composition_surface() -> None:
    """The runtime must not import T02 implementation modules directly."""

    telegram = import_module("friendly_bot.telegram")
    services = import_module("friendly_bot.services")

    missing = {
        "telegram": {
            name
            for name in (
                "TelegramGateway",
                "TelegramPoller",
                "OutboundDeliveryWorker",
            )
            if not hasattr(telegram, name)
        },
        "services": {
            name
            for name in (
                "ServiceAttendanceService",
                "ServiceDeliveryScheduler",
                "ServiceLifecycleService",
            )
            if not hasattr(services, name)
        },
    }

    assert missing == {"telegram": set(), "services": set()}
