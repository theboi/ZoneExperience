"""Recursive, declarative discussion-flow definitions."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from friendly_bot.domain.actions import DiscussionAction
from friendly_bot.domain.events import StableKey
from friendly_bot.domain.triggers import DiscussionFlowTrigger


class NextFlowMode(StrEnum):
    """How a selected flow keeps its children available."""

    ONE_AND_ONCE_ONLY = "one_and_once_only"
    ALLOW_MANY = "allow_many"
    CHECKPOINT = "checkpoint"


class DiscussionFlow(BaseModel):
    """One node in a recursively authored discussion-flow definition."""

    model_config = ConfigDict(extra="forbid")

    key: StableKey
    trigger: DiscussionFlowTrigger | None = None
    actions: list[DiscussionAction] = Field(default_factory=list)
    next_flows: list[DiscussionFlow] = Field(default_factory=list)
    next_flow_mode: NextFlowMode
    return_actions: list[DiscussionAction] = Field(default_factory=list)
