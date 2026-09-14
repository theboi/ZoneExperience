"""Closed concrete-action executor registration for I04 composition."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast, get_args

from friendly_bot.actions.context import ActionContext
from friendly_bot.domain.actions import DiscussionAction, DiscussionActionBase
from friendly_bot.matching.service import MatchingService
from friendly_bot.persistence.repositories import DiagnosticRepository
from friendly_bot.services import ServiceAttendanceService, ServiceLifecycleService
from friendly_bot.telegram import TelegramGateway

type ActionExecutor[A: DiscussionActionBase] = Callable[
    [A, ActionContext], Awaitable[None]
]


class DuplicateActionExecutorError(ValueError):
    """Raised when composition tries to register an action class twice."""


class UnregisteredActionExecutorError(LookupError):
    """Raised when a persisted concrete action has no exact executor."""


class ActionRegistryCompletenessError(ValueError):
    """Raised when startup sees a missing or extra concrete executor."""

    def __init__(
        self,
        *,
        missing: frozenset[type[DiscussionActionBase]],
        extra: frozenset[type[DiscussionActionBase]],
    ) -> None:
        self.missing = missing
        self.extra = extra

        def names(action_types: frozenset[type[DiscussionActionBase]]) -> str:
            return ", ".join(sorted(action.__name__ for action in action_types))

        super().__init__(f"missing=[{names(missing)}] extra=[{names(extra)}]")


@dataclass(frozen=True, slots=True)
class ActionDependencies:
    """The explicit upstream services shared by concrete action executors."""

    telegram: TelegramGateway
    services: ServiceAttendanceService
    lifecycle: ServiceLifecycleService
    matching: MatchingService
    diagnostics: DiagnosticRepository


class ActionExecutorRegistry:
    """Map only exact F01 concrete action classes to typed local executors."""

    def __init__(self) -> None:
        self._executors: dict[
            type[DiscussionActionBase], ActionExecutor[DiscussionActionBase]
        ] = {}

    def register[A: DiscussionActionBase](
        self, action_type: type[A], executor: ActionExecutor[A]
    ) -> None:
        if action_type in self._executors:
            raise DuplicateActionExecutorError(action_type.__name__)
        self._executors[action_type] = cast(
            ActionExecutor[DiscussionActionBase], executor
        )

    def resolve(self, action: DiscussionAction) -> ActionExecutor[DiscussionActionBase]:
        try:
            return self._executors[type(action)]
        except KeyError as error:
            raise UnregisteredActionExecutorError(type(action).__name__) from error

    def assert_complete(
        self, action_types: frozenset[type[DiscussionActionBase]]
    ) -> None:
        registered = frozenset(self._executors)
        if registered != action_types:
            raise ActionRegistryCompletenessError(
                missing=action_types - registered,
                extra=registered - action_types,
            )


def concrete_action_types(
    action_union: object,
) -> frozenset[type[DiscussionActionBase]]:
    """Derive the exact concrete action set from F01's discriminated union."""

    annotated_arguments = get_args(action_union)
    if not annotated_arguments:
        raise TypeError("expected an annotated F01 discussion-action union")
    action_types = frozenset(
        action_type
        for action_type in get_args(annotated_arguments[0])
        if isinstance(action_type, type)
        and issubclass(action_type, DiscussionActionBase)
    )
    if not action_types:
        raise TypeError("discussion-action union contains no concrete action classes")
    return action_types
