"""F01 public contracts required to end one service interaction safely."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from friendly_bot.persistence.repositories import (
    MatchRepository,
    OpenSelectionRepository,
    ServiceBoundSelectionExpiry,
    ServiceInteractionClosedError,
    ServiceRepository,
)


def test_service_expiry_contract_exposes_typed_users_and_scoped_release() -> None:
    """A count alone cannot route checkpoint return and a rematch release is unsafe."""

    affected_user_ids = frozenset({uuid4(), uuid4()})
    expiry = ServiceBoundSelectionExpiry(3, affected_user_ids)

    assert expiry.expired_selection_count == 3
    assert expiry.affected_user_ids == affected_user_ids
    with pytest.raises(FrozenInstanceError):
        expiry.expired_selection_count = 0  # type: ignore[misc]
    assert "expire_service_bound" in OpenSelectionRepository.__dict__
    assert "release_service_bound" in MatchRepository.__dict__


def test_service_closure_contract_exposes_a_typed_fence_error() -> None:
    """Service-bound writers must distinguish closure from unrelated repository errors."""

    service_id = uuid4()
    error = ServiceInteractionClosedError(service_id)

    assert error.service_id == service_id
    assert "claim_interaction_closure" in ServiceRepository.__dict__
