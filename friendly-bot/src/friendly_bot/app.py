"""I04 seed publication and runtime composition primitives."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from friendly_bot.domain.flows import DiscussionFlow
from friendly_bot.domain.publication import (
    PublishedFlowDefinition,
    RootKind,
    TemplateContextSchema,
    validate_for_publication,
)
from friendly_bot.persistence.models import FlowScopeKind, ServiceAudience
from friendly_bot.persistence.repositories import (
    FlowVersionRecord,
    NewService,
    NewServiceTimestamp,
    ServiceRecord,
    ServiceTimestampRecord,
)
from friendly_bot.persistence.uow import UnitOfWork

_ZONE_X_TEMPLATE_CONTEXT: Final = TemplateContextSchema(
    {
        "active_service.id",
        "human_match_request.interest",
        "matched_human.name",
        "matched_human.telegram_url",
        "matched_server.cg_name",
        "matched_server.name",
        "matched_server.telegram_url",
        "service.id",
        "service.map_url",
        "service.name",
        "user.display_name",
        "user.name",
    }
)


class ZoneXTimestampSeed(BaseModel):
    """One canonical timestamp root decoded from the JSON source of truth."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    occurs_at: datetime
    audience: ServiceAudience
    root_flow: DiscussionFlow


class ZoneXServiceSeed(BaseModel):
    """One service and every root I04 must publish without ORM access."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    name: str
    map_url: str
    timezone: str
    highkey: bool
    doors_open_at: datetime
    doors_close_at: datetime
    service_starts_at: datetime
    service_ends_at: datetime
    interaction_ends_at: datetime
    service_global_root: DiscussionFlow
    latecomer_flow: DiscussionFlow
    timestamps: tuple[ZoneXTimestampSeed, ...] = Field(min_length=1)


class ZoneXSeed(BaseModel):
    """The complete canonical Zone X JSON document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_global_root_excerpt: DiscussionFlow
    service: ZoneXServiceSeed


@dataclass(frozen=True, slots=True)
class PublishedZoneX:
    """The immutable flow records and live service identity needed for runtime entry."""

    service: ServiceRecord
    map_url: str
    system_root: FlowVersionRecord
    service_root: FlowVersionRecord
    latecomer_root: FlowVersionRecord
    timestamps: tuple[ServiceTimestampRecord, ...]

    def timestamp_for_key(self, key: str) -> ServiceTimestampRecord:
        """Return one canonical timestamp by stable source key."""

        for timestamp in self.timestamps:
            if timestamp.key == key:
                return timestamp
        raise LookupError("Zone X timestamp was not found")


def load_zone_x_seed(path: Path) -> ZoneXSeed:
    """Decode only the exact JSON seed shape; YAML is never a runtime configuration input."""

    if not isinstance(path, Path):
        raise TypeError("Zone X seed path must be a pathlib path")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ZoneXSeed.model_validate(_normalize_seed_document(raw))


async def publish_zone_x_seed(
    seed: ZoneXSeed, unit_of_work: UnitOfWork
) -> PublishedZoneX:
    """Validate and publish Zone X through F01's public immutable/version seed boundary."""

    if not isinstance(seed, ZoneXSeed):
        raise TypeError("Zone X seed is invalid")
    service = await unit_of_work.services.upsert(
        NewService(
            key=seed.service.key,
            name=seed.service.name,
            timezone=seed.service.timezone,
            highkey=seed.service.highkey,
            doors_open_at=seed.service.doors_open_at,
            doors_close_at=seed.service.doors_close_at,
            service_starts_at=seed.service.service_starts_at,
            service_ends_at=seed.service.service_ends_at,
            interaction_ends_at=seed.service.interaction_ends_at,
        )
    )
    system_root = await _publish_root(
        seed.system_global_root_excerpt,
        unit_of_work=unit_of_work,
        scope_kind=FlowScopeKind.SYSTEM,
        service_id=None,
        root_kind=RootKind.SYSTEM,
    )
    service_root = await _publish_root(
        seed.service.service_global_root,
        unit_of_work=unit_of_work,
        scope_kind=FlowScopeKind.SERVICE,
        service_id=service.id,
        root_kind=RootKind.SERVICE,
    )
    latecomer_root = await _publish_root(
        seed.service.latecomer_flow,
        unit_of_work=unit_of_work,
        scope_kind=FlowScopeKind.SERVICE,
        service_id=service.id,
        root_kind=RootKind.TIMESTAMP,
    )
    timestamps: list[ServiceTimestampRecord] = []
    for timestamp in seed.service.timestamps:
        flow_version = await _publish_root(
            timestamp.root_flow,
            unit_of_work=unit_of_work,
            scope_kind=FlowScopeKind.TIMESTAMP,
            service_id=service.id,
            root_kind=RootKind.TIMESTAMP,
        )
        timestamps.append(
            await unit_of_work.services.upsert_timestamp(
                NewServiceTimestamp(
                    key=timestamp.key,
                    occurs_at=timestamp.occurs_at,
                    audience=timestamp.audience,
                    flow_version_id=flow_version.id,
                    root_flow_key=str(timestamp.root_flow.key),
                ),
                service_id=service.id,
            )
        )
    return PublishedZoneX(
        service=service,
        map_url=seed.service.map_url,
        system_root=system_root,
        service_root=service_root,
        latecomer_root=latecomer_root,
        timestamps=tuple(timestamps),
    )


async def _publish_root(
    root: DiscussionFlow,
    *,
    unit_of_work: UnitOfWork,
    scope_kind: FlowScopeKind,
    service_id: UUID | None,
    root_kind: RootKind,
) -> FlowVersionRecord:
    published: PublishedFlowDefinition = validate_for_publication(
        root, _ZONE_X_TEMPLATE_CONTEXT, root_kind
    )
    return await unit_of_work.flow_versions.publish(
        published,
        scope_kind=scope_kind,
        service_id=service_id,
        published_by_user_id=None,
    )


def _normalize_seed_document(value: object) -> object:
    """Translate only canonical YAML enum spellings to the existing F01 wire values."""

    if isinstance(value, list):
        return [_normalize_seed_document(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if key in {"next_flow_mode", "audience"} and isinstance(item, str):
            normalized[key] = item.lower()
        else:
            normalized[key] = _normalize_seed_document(item)
    return normalized
