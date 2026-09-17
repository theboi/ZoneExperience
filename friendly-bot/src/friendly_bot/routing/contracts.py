"""Prompt-safe values crossing from application policy to the model gateway."""

from __future__ import annotations

from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class PromptDTO(BaseModel):
    """Reject fields that are not explicitly safe for an external prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ReplySourceSlot(PromptDTO):
    """One model-visible answer slot with its permitted configured source."""

    slot_id: str = Field(pattern=r"^r[0-9]+$")
    mode: Literal["paraphrased", "llm"]
    source: str = Field(min_length=1)
    source_template_tokens: tuple[str, ...] = ()
    source_urls: tuple[str, ...] = ()


class RoutingPromptCandidate(PromptDTO):
    """One prompt-safe typed-routing candidate without durable identity."""

    flow_id: str = Field(min_length=1, validation_alias=AliasChoices("flow_id", "key"))
    gists: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("gists", "gist")
    )
    possible_qns: tuple[str, ...] = ()
    context_label: str = Field(min_length=1)
    multi_intent_mode: Literal["answer", "interactive"] = "interactive"
    reply_slots: tuple[ReplySourceSlot, ...] = ()

    @field_validator("gists", mode="before")
    @classmethod
    def normalize_legacy_gist(cls, value: object) -> object:
        """Accept legacy key-selection fixtures until the gateway migration lands."""

        return (value,) if isinstance(value, str) else value

    @model_validator(mode="after")
    def has_routing_hint(self) -> RoutingPromptCandidate:
        """Require a semantic gist, concrete possible question, or both."""

        if not self.gists and not self.possible_qns:
            raise ValueError("routing candidate requires gists or possible_qns")
        return self

    @property
    def key(self) -> str:
        """Expose the legacy key-selection name during the staged migration."""

        return self.flow_id

    @property
    def gist(self) -> str:
        """Expose the first routing hint for the legacy key selector."""

        return (self.gists or self.possible_qns)[0]


class KeySelectionRequest(PromptDTO):
    """The complete model-visible input for one constrained routing decision."""

    allowed_keys: frozenset[str] = Field(min_length=1)
    user_name: str = ""
    persona: str = ""
    messages: tuple[str, ...] = ()
    reply_body: str | None = None
    candidates: tuple[RoutingPromptCandidate, ...] = ()


class PlannedReply(PromptDTO):
    """One model-proposed replacement for a deterministic reply slot."""

    slot_id: str = Field(pattern=r"^r[0-9]+$")
    text: str = Field(min_length=1, max_length=4096)


class PlannedFlowMatch(PromptDTO):
    """One configured flow and every reply slot it must supply."""

    flow_id: str = Field(min_length=1)
    replies: tuple[PlannedReply, ...] = ()


class MultiIntentMatches(PromptDTO):
    """One ordered, bounded set of model-selected configured flow matches."""

    kind: Literal["matches"]
    matches: tuple[PlannedFlowMatch, ...] = Field(min_length=1, max_length=5)


class MultiIntentTerminal(PromptDTO):
    """The only terminal outcomes for a one-shot typed routing operation."""

    kind: Literal["terminal"]
    terminal: Literal["no_match", "clarify_ambiguous_context"]


class MultiIntentProviderPlan(PromptDTO):
    """The flat structured-output contract accepted by Gemini through OpenRouter."""

    kind: Literal["matches", "terminal"]
    matches: tuple[PlannedFlowMatch, ...] = Field(max_length=5)
    terminal: Literal[
        "not_applicable", "no_match", "clarify_ambiguous_context"
    ]


type MultiIntentModelResult = MultiIntentMatches | MultiIntentTerminal


class MultiIntentRequest(PromptDTO):
    """The complete safe input for one all-at-once typed routing operation."""

    persona: str = ""
    messages: tuple[str, ...] = ()
    reply_body: str | None = None
    candidates: tuple[RoutingPromptCandidate, ...] = Field(min_length=1)


class KnownFlowRequest(PromptDTO):
    """The safe request for an LLM reply to one already-determined configured flow."""

    flow_id: str = Field(min_length=1)
    reply_slots: tuple[ReplySourceSlot, ...] = ()
    messages: tuple[str, ...] = ()


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
