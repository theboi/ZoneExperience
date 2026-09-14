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
    build_action_registry,
    concrete_action_types,
)
from friendly_bot.actions.runner import (
    DEFAULT_UNHANDLED_ERROR_TEXT,
    ActionRunner,
    ActionRunResult,
    DirectEventHandlerInvariantError,
    RetryableActionExecutionError,
    send_unhandled_action_error,
)

__all__ = [
    "DEFAULT_UNHANDLED_ERROR_TEXT",
    "ActionContext",
    "ActionDependencies",
    "ActionExecutorRegistry",
    "ActionRegistryCompletenessError",
    "ActionRunResult",
    "ActionRunner",
    "DirectEventHandlerInvariantError",
    "DuplicateActionExecutorError",
    "ReservedActionEventError",
    "RetryableActionExecutionError",
    "TerminalActionEventAlreadyEmittedError",
    "UnregisteredActionExecutorError",
    "build_action_registry",
    "concrete_action_types",
    "send_unhandled_action_error",
]
