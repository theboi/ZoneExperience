"""Closed trigger models for persisted discussion-flow configuration."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from friendly_bot.domain.events import StableKey

StableButtonId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DiscussionTriggerBase(BaseModel):
    """Common validation for stored trigger configuration."""

    model_config = ConfigDict(extra="forbid")


class OnMessageTrigger(DiscussionTriggerBase):
    type: Literal["message"]
    llm_gist: NonEmptyText | None = None
    possible_qns: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def has_routing_description(self) -> OnMessageTrigger:
        """Require exactly one routing-description form."""

        if (self.llm_gist is None) == (not self.possible_qns):
            raise ValueError(
                "message trigger requires exactly one of llm_gist or possible_qns"
            )
        return self


class OnButtonPressTrigger(DiscussionTriggerBase):
    type: Literal["button"]
    button_id: StableButtonId


class AutomaticTrigger(DiscussionTriggerBase):
    type: Literal["automatic"]


class OnActionEventTrigger(DiscussionTriggerBase):
    type: Literal["action_event"]
    event_key: StableKey


RegisteredDiscussionTrigger = Annotated[
    OnMessageTrigger
    | OnButtonPressTrigger
    | AutomaticTrigger
    | OnActionEventTrigger,
    Field(discriminator="type"),
]


class OnAnyOfTrigger(DiscussionTriggerBase):
    type: Literal["any_of"]
    triggers: list[RegisteredDiscussionTrigger] = Field(min_length=2)

    @field_validator("triggers")
    @classmethod
    def triggers_are_distinct(
        cls, value: list[RegisteredDiscussionTrigger]
    ) -> list[RegisteredDiscussionTrigger]:
        normalized = [trigger.model_dump_json() for trigger in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("any_of triggers must be distinct")
        return value


DiscussionTrigger = Annotated[
    RegisteredDiscussionTrigger | OnAnyOfTrigger,
    Field(discriminator="type"),
]

_trigger_adapter: TypeAdapter[DiscussionTrigger] = TypeAdapter(DiscussionTrigger)


def parse_trigger(data: dict[str, object]) -> DiscussionTrigger:
    """Parse one persisted trigger using the registered discriminator set."""

    return _trigger_adapter.validate_python(data)
