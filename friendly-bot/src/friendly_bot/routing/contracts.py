"""Prompt-safe values crossing from application policy to the model gateway."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PromptDTO(BaseModel):
    """Reject fields that are not explicitly safe for an external prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RoutingPromptCandidate(PromptDTO):
    """A configured key and human-written routing hint, without durable identity."""

    key: str = Field(min_length=1)
    gist: str = Field(min_length=1)
    context_label: str = Field(min_length=1)


class KeySelectionRequest(PromptDTO):
    """The complete model-visible input for one constrained routing decision."""

    allowed_keys: frozenset[str] = Field(min_length=1)
    user_name: str = ""
    persona: str = ""
    messages: tuple[str, ...] = ()
    reply_body: str | None = None
    candidates: tuple[RoutingPromptCandidate, ...] = ()


class PersonaSummaryRequest(PromptDTO):
    """The safe, content-only input for one durable persona summary."""

    user_name: str = ""
    persona: str = ""
    messages: tuple[str, ...] = Field(min_length=1)


class MatchPromptCandidate(PromptDTO):
    """A locally mapped match alias and the only profile attributes safe to rank."""

    alias: str = Field(min_length=1)
    interests: tuple[str, ...] = ()
    cg_name: str | None = None


class MatchRankingRequest(PromptDTO):
    """An alias-only candidate pool for the external model ranking boundary."""

    candidates: tuple[MatchPromptCandidate, ...] = Field(min_length=1)
