"""Application policy for bounded deferred interactive flow references."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from friendly_bot.persistence.repositories import (
    NewPendingFlowIntent,
    PendingFlowIntentRecord,
    PendingIntentRepository,
)

_PENDING_INTENT_TTL = timedelta(hours=24)
_MAX_PENDING_INTENTS_PER_USER = 5


class PendingIntentService:
    """Queue only typed flow references after the caller serializes the user."""

    def __init__(self, repository: PendingIntentRepository) -> None:
        self._repository = repository

    async def enqueue(
        self,
        *,
        user_id: UUID,
        flow_key: str,
        flow_version_id: UUID,
        service_id: UUID | None,
        now: datetime,
    ) -> PendingFlowIntentRecord:
        """Append or refresh one deferred flow with a fixed 24-hour lifetime."""

        return await self._repository.append(
            NewPendingFlowIntent(
                user_id=user_id,
                flow_key=flow_key,
                flow_version_id=flow_version_id,
                service_id=service_id,
                created_at=now,
                expires_at=now + _PENDING_INTENT_TTL,
            ),
            max_per_user=_MAX_PENDING_INTENTS_PER_USER,
        )

    async def list_active(
        self, user_id: UUID, *, now: datetime
    ) -> list[PendingFlowIntentRecord]:
        """Prune expired references before returning the user's ordered queue."""

        await self._repository.delete_expired(user_id, now=now)
        return await self._repository.list_active(user_id, now=now)

    async def remove(self, intent_id: UUID) -> None:
        """Remove the one queued reference already selected for resumption."""

        await self._repository.delete(intent_id)
