"""I04's closed action-composition primitives."""

from friendly_bot.actions.context import (
    ActionContext,
    ReservedActionEventError,
    TerminalActionEventAlreadyEmittedError,
)
from friendly_bot.actions.registry import (
    ActionDependencies,
    ActionExecutorRegistry,
    ActionRegistryCompletenessError,
    DuplicateActionExecutorError,
    UnregisteredActionExecutorError,
    concrete_action_types,
)

__all__ = [
    "ActionContext",
    "ActionDependencies",
    "ActionExecutorRegistry",
    "ActionRegistryCompletenessError",
    "DuplicateActionExecutorError",
    "ReservedActionEventError",
    "TerminalActionEventAlreadyEmittedError",
    "UnregisteredActionExecutorError",
    "concrete_action_types",
]
