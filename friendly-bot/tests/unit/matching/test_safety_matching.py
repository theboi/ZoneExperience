"""Safety responder policy stays separate from ordinary human matching."""

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


def _candidate(
    role: OperationalRole, *, always_available: bool = False
) -> MatchCandidateRecord:
    return MatchCandidateRecord(
        profile_id=uuid4(),
        user_id=uuid4(),
        role=role,
        interests=("calm",),
        cg_name="safe",
        always_available=always_available,
        capacity=1,
        reserved_capacity=0,
    )


class SafetyUow:
    def __init__(self, candidates: list[MatchCandidateRecord]) -> None:
        self.matches = self
        self.candidates = candidates
        self.normal_queries = 0
        self.safety_queries = 0
        self.locked_user_ids: list[UUID] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def lock_user(self, user_id: UUID) -> None:
        self.locked_user_ids.append(user_id)

    async def list_eligible_safety(
        self, service_id: UUID | None, request_id: UUID
    ) -> list[MatchCandidateRecord]:
        assert (service_id, request_id) == (SERVICE, REQUEST)
        self.safety_queries += 1
        return self.candidates

    async def list_eligible_normal(
        self, service_id: UUID, request_id: UUID
    ) -> list[MatchCandidateRecord]:
        self.normal_queries += 1
        raise AssertionError(
            f"safety must not fall back to normal: {service_id} {request_id}"
        )

    async def reserve_ranked(
        self, request_id: UUID, profile_ids: list[UUID], *, now: datetime
    ) -> MatchAssignmentRecord | None:
        assert request_id == REQUEST and now == NOW
        self.ranked_profile_ids = profile_ids
        return (
            MatchAssignmentRecord(uuid4(), REQUEST, profile_ids[0], NOW)
            if profile_ids
            else None
        )


class SafetyRanker:
    async def rank_aliases(self, request: MatchRankingRequest) -> list[str]:
        self.request = request
        return [candidate.alias for candidate in reversed(request.candidates)]


async def test_safety_allows_leader_or_staff_attending_or_always_available() -> None:
    """Servers never enter safety ranking, even when the repository returns one."""

    server = _candidate(OperationalRole.SERVER, always_available=True)
    leader = _candidate(OperationalRole.LEADER)
    staff_always_available = _candidate(OperationalRole.STAFF, always_available=True)
    uow = SafetyUow([server, leader, staff_always_available])
    ranker = SafetyRanker()
    service = MatchingService(lambda: uow, ranker, lambda _: REQUESTER)

    assignment = await service.reserve_safety(REQUEST, SERVICE, now=NOW)

    assert assignment is not None
    assert assignment.responder_profile_id == staff_always_available.profile_id
    assert uow.ranked_profile_ids == [
        staff_always_available.profile_id,
        leader.profile_id,
    ]
    assert uow.normal_queries == 0
    assert uow.safety_queries == 1
    assert uow.locked_user_ids == [REQUESTER]


async def test_safety_with_no_candidates_returns_none_without_normal_fallback() -> None:
    """Urgent support has no ordinary-server fallback path."""

    uow = SafetyUow([])
    service = MatchingService(lambda: uow, SafetyRanker(), lambda _: REQUESTER)

    assert await service.reserve_safety(REQUEST, SERVICE, now=NOW) is None
    assert uow.normal_queries == 0
