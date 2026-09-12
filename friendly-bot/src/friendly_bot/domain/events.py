"""Internal outcomes emitted by declarative actions."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, JsonValue, StringConstraints

StableKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")]


class ActionEvent(BaseModel):
    """A single action outcome used to select a direct child flow."""

    model_config = ConfigDict(extra="forbid")

    key: StableKey
    payload: dict[str, JsonValue] | None = None
