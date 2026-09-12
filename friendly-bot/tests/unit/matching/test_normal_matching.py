"""Normal human-match policy and requester-serialized reservation coverage."""

from __future__ import annotations

from dataclasses import dataclass
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
    role: OperationalRole,
    *,
    capacity: int = 1,
    reserved_capacity: int = 0,
) -> MatchCandidateRecord:
    return MatchCandidateRecord(
        profile_id=uuid4(),
        user_id=uuid4(),
        role=role,
        interests=("music",),
        cg_name="friendly",
        always_available=False,
        capacity=capacity,
        reserved_capacity=reserved_capacity,
    )


@dataclass
class Ranker:
    aliases: list[str]
    requests: list[MatchRankingRequest]

    def __init__(self, aliases: list[str]) -> None:
        self.aliases = aliases
        self.requests = []

    async def rank_aliases(self, request: MatchRankingRequest) -> list[str]:
        self.requests.append(request)
        return self.aliases


class FakeUow:
    def __init__(
        self,
        candidates: list[MatchCandidateRecord],
        assignment: MatchAssignmentRecord | None,
    ) -> None:
        self.matches = self
        self.candidates = candidates
        self.assignment = assignment
        self.calls: list[str] = []
        self.locked_user_ids: list[UUID] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def lock_user(self, user_id: UUID) -> None:
        self.locked_user_ids.append(user_id)

    async def list_eligible_normal(
        self, service_id: UUID, request_id: UUID
    ) -> list[MatchCandidateRecord]:
        assert (service_id, request_id) == (SERVICE, REQUEST)
        self.calls.append("list")
        return self.candidates

    async def reserve_ranked(
        self, request_id: UUID, profile_ids: list[UUID], *, now: datetime
    ) -> MatchAssignmentRecord | None:
        assert request_id == REQUEST and now == NOW
        self.calls.append("reserve")
        self.ranked_profile_ids = profile_ids
        return self.assignment

    async def release_and_exclude(
        self, request_id: UUID, profile_id: UUID, *, reason: str, now: datetime
    ) -> None:
        assert request_id == REQUEST and now == NOW
        self.calls.append("release")
        self.released_profile_id = profile_id
        self.release_reason = reason


async def test_normal_pool_is_exact_server_attendees_with_capacity() -> None:
    """Leaders, staff, and exhausted records must never reach the rank prompt."""

    attending_server = _candidate(OperationalRole.SERVER)
    leader = _candidate(OperationalRole.LEADER)
    staff = _candidate(OperationalRole.STAFF)
    exhausted_server = _candidate(OperationalRole.SERVER, reserved_capacity=1)
    assignment = MatchAssignmentRecord(uuid4(), REQUEST, attending_server.profile_id, NOW)
    uow = FakeUow([attending_server, leader, staff, exhausted_server], assignment)
    ranker = Ranker(["candidate-0"])
    service = MatchingService(lambda: uow, ranker, lambda _: REQUESTER)

    result = await service.reserve_normal(REQUEST, SERVICE, now=NOW)

    assert result == assignment
    assert uow.locked_user_ids == [REQUESTER]
    assert uow.ranked_profile_ids == [attending_server.profile_id]
    assert [candidate.alias for candidate in ranker.requests[0].candidates] == [
        "candidate-0"
    ]
    assert not hasattr(ranker.requests[0].candidates[0], "profile_id")


async def test_rematch_releases_and_excludes_before_new_reservation() -> None:
    """A declined responder must be excluded before the new rank request is built."""

    old_profile_id = uuid4()
    replacement = _candidate(OperationalRole.SERVER)
    assignment = MatchAssignmentRecord(uuid4(), REQUEST, replacement.profile_id, NOW)
    uow = FakeUow([replacement], assignment)
    service = MatchingService(lambda: uow, Ranker(["candidate-0"]), lambda _: REQUESTER)

    result = await service.rematch_normal(
        REQUEST, old_profile_id, SERVICE, now=NOW, reason="declined"
    )

    assert result == assignment
    assert uow.calls == ["release", "list", "reserve"]
    assert uow.released_profile_id == old_profile_id
    assert uow.release_reason == "declined"
