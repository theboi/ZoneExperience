"""R03 matching integration seam tests; PostgreSQL race coverage is added with safety."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Self
from uuid import UUID, uuid4

from friendly_bot.matching.service import MatchingService
from friendly_bot.persistence.models import OperationalRole
from friendly_bot.persistence.repositories import (
    MatchAssignmentRecord,
    MatchCandidateRecord,
)
from friendly_bot.routing.contracts import MatchRankingRequest

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
REQUEST = uuid4()
SERVICE = uuid4()
REQUESTER = uuid4()


class ReservationUow:
    def __init__(self, candidate: MatchCandidateRecord) -> None:
        self.matches = self
        self.candidate = candidate
        self.lock_calls = 0
        self.reserved = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def lock_user(self, user_id: UUID) -> None:
        assert user_id == REQUESTER
        self.lock_calls += 1

    async def list_eligible_normal(
        self, service_id: UUID, request_id: UUID
    ) -> list[MatchCandidateRecord]:
        assert (service_id, request_id) == (SERVICE, REQUEST)
        return [self.candidate]

    async def reserve_ranked(
        self, request_id: UUID, profile_ids: list[UUID], *, now: datetime
    ) -> MatchAssignmentRecord:
        assert (request_id, profile_ids, now) == (REQUEST, [self.candidate.profile_id], NOW)
        self.reserved = True
        return MatchAssignmentRecord(uuid4(), REQUEST, self.candidate.profile_id, NOW)


class AliasRanker:
    async def rank_aliases(self, request: MatchRankingRequest) -> list[str]:
        assert [candidate.alias for candidate in request.candidates] == ["candidate-0"]
        return ["candidate-0"]


async def test_normal_reservation_uses_f01_guarded_repository_under_user_lock() -> None:
    """R03 delegates capacity mutation to the F01 guarded repository call."""

    candidate = MatchCandidateRecord(
        profile_id=uuid4(),
        user_id=uuid4(),
        role=OperationalRole.SERVER,
        interests=("music",),
        cg_name="friendly",
        always_available=False,
        capacity=1,
        reserved_capacity=0,
    )
    uow = ReservationUow(candidate)

    assignment = await MatchingService(
        lambda: uow, AliasRanker(), lambda _: REQUESTER
    ).reserve_normal(REQUEST, SERVICE, now=NOW)

    assert assignment is not None
    assert uow.lock_calls == 1
    assert uow.reserved is True
