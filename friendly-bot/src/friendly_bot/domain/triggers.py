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
)

from friendly_bot.domain.events import StableKey

StableButtonId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DiscussionFlowTriggerBase(BaseModel):
    """Common validation for stored trigger configuration."""

    model_config = ConfigDict(extra="forbid")


class MessageDiscussionFlowTrigger(DiscussionFlowTriggerBase):
    type: Literal["message"]
    llm_gist: NonEmptyText


class ButtonDiscussionFlowTrigger(DiscussionFlowTriggerBase):
    type: Literal["button"]
    button_id: StableButtonId


class CommandDiscussionFlowTrigger(DiscussionFlowTriggerBase):
    type: Literal["command"]
    command: NonEmptyText

    @field_validator("command")
    @classmethod
    def command_is_normalized(cls, value: str) -> str:
        if not value.startswith("/") or "@" in value:
            raise ValueError("command must start with '/' and omit any bot suffix")
        return value


class AutomaticDiscussionFlowTrigger(DiscussionFlowTriggerBase):
    type: Literal["automatic"]


class ActionEventDiscussionFlowTrigger(DiscussionFlowTriggerBase):
    type: Literal["action_event"]
    event_key: StableKey


RegisteredDiscussionFlowTrigger = Annotated[
    MessageDiscussionFlowTrigger
    | ButtonDiscussionFlowTrigger
    | CommandDiscussionFlowTrigger
    | AutomaticDiscussionFlowTrigger
    | ActionEventDiscussionFlowTrigger,
    Field(discriminator="type"),
]


class AnyOfDiscussionFlowTrigger(DiscussionFlowTriggerBase):
    type: Literal["any_of"]
    triggers: list[RegisteredDiscussionFlowTrigger] = Field(min_length=2)

    @field_validator("triggers")
    @classmethod
    def triggers_are_distinct(
        cls, value: list[RegisteredDiscussionFlowTrigger]
    ) -> list[RegisteredDiscussionFlowTrigger]:
        normalized = [trigger.model_dump_json() for trigger in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("any_of triggers must be distinct")
        return value


DiscussionFlowTrigger = Annotated[
    RegisteredDiscussionFlowTrigger | AnyOfDiscussionFlowTrigger,
    Field(discriminator="type"),
]

_trigger_adapter: TypeAdapter[DiscussionFlowTrigger] = TypeAdapter(
    DiscussionFlowTrigger
)


def parse_trigger(data: dict[str, object]) -> DiscussionFlowTrigger:
    """Parse one persisted trigger using the registered discriminator set."""

    return _trigger_adapter.validate_python(data)
