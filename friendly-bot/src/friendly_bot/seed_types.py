"""Static authoring types for JSON-safe Python seed objects."""

from __future__ import annotations

from typing import NotRequired, TypedDict

type SeedScalar = str | int | float | bool | None
type SeedValue = SeedScalar | list[SeedValue] | dict[str, SeedValue]
type SeedObject = dict[str, SeedValue]


class SeedFlow(TypedDict):
    """The statically checked recursive shell shared by every flow definition."""

    key: str
    trigger: SeedObject | None
    actions: list[SeedObject]
    next_flow_mode: str
    return_actions: list[SeedObject]
    next_flows: list[SeedFlow]
    multi_intent_mode: NotRequired[str]


class SystemGlobalSeedDocument(TypedDict):
    """The one system-global flow root."""

    root: SeedFlow
    operational_profiles: NotRequired[list[OperationalProfileSeedDocument]]


class OperationalProfileSeedDocument(TypedDict):
    """One pre-authorized operational profile provisioned at application startup."""

    name: str
    dob: str
    role: str
    interests: list[str]
    cg_name: str | None
    telegram_contact_url: str | None
    always_available: bool
    capacity: int
    is_admin: bool


class ZoneXTimestampSeedDocument(TypedDict):
    """One scheduled Zone X root."""

    key: str
    occurs_at: str
    audience: str
    root_flow: SeedFlow


class ZoneXServiceSeedDocument(TypedDict):
    """The static authoring shape for Zone X service configuration."""

    key: str
    name: str
    map_url: str
    timezone: str
    highkey: bool
    doors_open_at: str
    doors_close_at: str
    service_starts_at: str
    service_ends_at: str
    interaction_ends_at: str
    service_global_root: SeedFlow
    latecomer_flow: SeedFlow
    timestamps: list[ZoneXTimestampSeedDocument]


class ZoneXSeedDocument(TypedDict):
    """The one Zone X service seed document."""

    service: ZoneXServiceSeedDocument
