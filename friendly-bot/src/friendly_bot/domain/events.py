"""Internal outcomes emitted by declarative actions."""

import math
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    JsonValue,
    StringConstraints,
    field_validator,
)

StableKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")]


class ActionEvent(BaseModel):
    """A single action outcome used to select a direct child flow."""

    model_config = ConfigDict(extra="forbid")

    key: StableKey
    payload: dict[str, JsonValue] | None = None

    @field_validator("payload")
    @classmethod
    def payload_has_only_finite_floats(
        cls, value: dict[str, JsonValue] | None
    ) -> dict[str, JsonValue] | None:
        if value is not None:
            _reject_non_finite_floats(value, path="payload")
        return value


def _reject_non_finite_floats(value: JsonValue, *, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} must not contain a non-finite float")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_finite_floats(item, path=f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_non_finite_floats(item, path=f"{path}.{key}")
