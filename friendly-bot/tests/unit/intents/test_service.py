"""Behavioral contracts for bounded, structured pending flow intents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from friendly_bot.intents.service import PendingIntentService
from friendly_bot.persistence.repositories import (
    NewPendingFlowIntent,
    PendingFlowIntentRecord,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
USER = uuid4()
VERSION = uuid4()
SERVICE = uuid4()


@dataclass
class RecordingPendingIntents:
    appended: list[tuple[NewPendingFlowIntent, int]] = field(default_factory=list)
    expired: list[tuple[UUID, datetime]] = field(default_factory=list)
    deleted: list[UUID] = field(default_factory=list)
    active: list[PendingFlowIntentRecord] = field(default_factory=list)

    async def append(
        self, intent: NewPendingFlowIntent, *, max_per_user: int
    ) -> PendingFlowIntentRecord:
        self.appended.append((intent, max_per_user))
        record = PendingFlowIntentRecord(
            id=uuid4(),
            position=len(self.active),
            user_id=intent.user_id,
            flow_key=intent.flow_key,
            flow_version_id=intent.flow_version_id,
            service_id=intent.service_id,
            created_at=intent.created_at,
            expires_at=intent.expires_at,
        )
        self.active.append(record)
        return record

    async def list_active(
        self, user_id: UUID, *, now: datetime
    ) -> list[PendingFlowIntentRecord]:
        assert user_id == USER
        assert now == NOW
        return self.active

    async def delete_expired(self, user_id: UUID, *, now: datetime) -> int:
        self.expired.append((user_id, now))
        return 0

    async def delete(self, intent_id: UUID) -> None:
        self.deleted.append(intent_id)


async def test_enqueue_uses_a_bounded_day_long_structured_record() -> None:
    repository = RecordingPendingIntents()
    service = PendingIntentService(repository)

    record = await service.enqueue(
        user_id=USER,
        flow_key="service.zone_x.connect",
        flow_version_id=VERSION,
        service_id=SERVICE,
        now=NOW,
    )

    intent, cap = repository.appended[0]
    assert record.flow_key == "service.zone_x.connect"
    assert cap == 5
    assert intent.user_id == USER
    assert intent.flow_version_id == VERSION
    assert intent.service_id == SERVICE
    assert intent.created_at == NOW
    assert intent.expires_at == NOW + timedelta(hours=24)


async def test_list_active_prunes_before_returning_ordered_intents() -> None:
    repository = RecordingPendingIntents()
    service = PendingIntentService(repository)

    assert await service.list_active(USER, now=NOW) == []
    assert repository.expired == [(USER, NOW)]


async def test_remove_deletes_only_the_exact_pending_record() -> None:
    repository = RecordingPendingIntents()
    service = PendingIntentService(repository)
    intent_id = uuid4()

    await service.remove(intent_id)

    assert repository.deleted == [intent_id]
