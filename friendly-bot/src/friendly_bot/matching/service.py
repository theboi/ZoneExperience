"""Exact-role normal matching through F01's guarded reservation repository."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from friendly_bot.persistence.models import OperationalRole
from friendly_bot.persistence.repositories import (
    MatchAssignmentRecord,
    MatchCandidateRecord,
)
from friendly_bot.persistence.uow import UnitOfWork, UnitOfWorkFactory
from friendly_bot.routing.contracts import MatchPromptCandidate, MatchRankingRequest


class MatchingError(RuntimeError):
    """The model ranking cannot safely be mapped to an eligible profile."""


class MatchRanker(Protocol):
    """Rank local aliases, never durable profile identity."""

    async def rank_aliases(self, request: MatchRankingRequest) -> Sequence[str]: ...


class MatchingService:
    """Reserve normal responders only through F01's exact-role guarded operation."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ranker: MatchRanker,
        requester_user_for: Callable[[UUID], UUID],
    ) -> None:
        self._uow_factory = uow_factory
        self._ranker = ranker
        self._requester_user_for = requester_user_for

    async def reserve_normal(
        self, request_id: UUID, service_id: UUID, *, now: datetime
    ) -> MatchAssignmentRecord | None:
        """Rank only eligible exact-server candidates and reserve through F01."""

        async with self._uow_factory() as uow:
            await uow.lock_user(self._requester_user_for(request_id))
            return await self._reserve_normal_locked(uow, request_id, service_id, now)

    async def rematch_normal(
        self,
        request_id: UUID,
        previous_profile_id: UUID,
        service_id: UUID,
        *,
        now: datetime,
        reason: str = "rematch",
    ) -> MatchAssignmentRecord | None:
        """Release and exclude before re-ranking so a declined responder cannot recur."""

        async with self._uow_factory() as uow:
            await uow.lock_user(self._requester_user_for(request_id))
            await uow.matches.release_and_exclude(
                request_id, previous_profile_id, reason=reason, now=now
            )
            return await self._reserve_normal_locked(uow, request_id, service_id, now)

    async def reserve_safety(
        self, request_id: UUID, service_id: UUID | None, *, now: datetime
    ) -> MatchAssignmentRecord | None:
        """Reserve only eligible leader/staff responders; never fall back to normal."""

        async with self._uow_factory() as uow:
            await uow.lock_user(self._requester_user_for(request_id))
            candidates = await uow.matches.list_eligible_safety(service_id, request_id)
            eligible = [
                candidate for candidate in candidates if _is_safety_eligible(candidate)
            ]
            if not eligible:
                return None
            ranked_profile_ids = await self._rank_profile_ids(eligible)
            return await uow.matches.reserve_ranked(
                request_id, ranked_profile_ids, now=now
            )

    async def _reserve_normal_locked(
        self, uow: UnitOfWork, request_id: UUID, service_id: UUID, now: datetime
    ) -> MatchAssignmentRecord | None:
        candidates = await uow.matches.list_eligible_normal(service_id, request_id)
        eligible = [
            candidate for candidate in candidates if _is_normal_eligible(candidate)
        ]
        if not eligible:
            return None
        ranked_profile_ids = await self._rank_profile_ids(eligible)
        return await uow.matches.reserve_ranked(request_id, ranked_profile_ids, now=now)

    async def _rank_profile_ids(
        self, candidates: Sequence[MatchCandidateRecord]
    ) -> list[UUID]:
        aliases = {
            f"candidate-{position}": candidate
            for position, candidate in enumerate(candidates)
        }
        request = MatchRankingRequest(
            candidates=tuple(
                MatchPromptCandidate(
                    alias=alias,
                    interests=candidate.interests,
                    cg_name=candidate.cg_name,
                )
                for alias, candidate in aliases.items()
            )
        )
        ranked_aliases = await self._ranker.rank_aliases(request)
        ranked_ids: list[UUID] = []
        for alias in ranked_aliases:
            candidate = aliases.get(alias)
            if candidate is None or candidate.profile_id in ranked_ids:
                raise MatchingError(
                    "ranker returned an invalid or duplicate candidate alias"
                )
            ranked_ids.append(candidate.profile_id)
        for candidate in aliases.values():
            if candidate.profile_id not in ranked_ids:
                ranked_ids.append(candidate.profile_id)
        return ranked_ids


def _is_normal_eligible(candidate: MatchCandidateRecord) -> bool:
    """Defend the exact normal-match policy at the R03 boundary too."""

    return (
        candidate.role is OperationalRole.SERVER
        and candidate.capacity > candidate.reserved_capacity
    )


def _is_safety_eligible(candidate: MatchCandidateRecord) -> bool:
    """Keep normal servers out even when an upstream query is misconfigured."""

    return (
        candidate.role in {OperationalRole.LEADER, OperationalRole.STAFF}
        and candidate.capacity > candidate.reserved_capacity
    )
