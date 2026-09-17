"""Validation and canonicalization for immutable flow publications."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, cast

from pydantic import JsonValue, ValidationError

from friendly_bot.domain.actions import (
    DiscussionAction,
    SendButtonsAction,
    SendMessageFixedAction,
    SendMessageLlmAction,
    SendMessageParaphrasedAction,
    SendPhotoAction,
    parse_action,
)
from friendly_bot.domain.flows import (
    DiscussionFlow,
    MultiIntentMode,
    NextFlowMode,
)
from friendly_bot.domain.templates import TEMPLATE_TOKEN_PATTERN
from friendly_bot.domain.triggers import (
    DiscussionTrigger,
    OnActionEventTrigger,
    parse_trigger,
)

_STABLE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_DOTTED_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class FlowPublicationError(ValueError):
    """Raised when a draft flow cannot become an immutable publication."""


class RootKind(StrEnum):
    """The application context that owns a published root definition."""

    SYSTEM = "system"
    SERVICE = "service"
    TIMESTAMP = "timestamp"


@dataclass(frozen=True)
class TemplateContextSchema:
    """Names that are legal to render in one published root."""

    permitted_names: frozenset[str]

    def __init__(self, permitted_names: Iterable[str]) -> None:
        names = frozenset(permitted_names)
        invalid_names = [
            name
            for name in names
            if not isinstance(name, str) or _DOTTED_NAME_PATTERN.fullmatch(name) is None
        ]
        if invalid_names:
            raise ValueError("template context names must be dotted paths")
        object.__setattr__(self, "permitted_names", names)


@dataclass(frozen=True)
class PublicationWarning:
    """A non-fatal finding retained with a published definition."""

    code: str
    flow_key: str


@dataclass(frozen=True, init=False)
class PublishedFlowDefinition:
    """The canonical data persisted as one immutable flow version."""

    _canonical_document: bytes
    _flow_key_index: Mapping[str, tuple[int, ...]]
    content_hash: str
    warnings: tuple[PublicationWarning, ...]

    def __init__(
        self,
        *,
        document: Mapping[str, JsonValue],
        flow_key_index: Mapping[str, tuple[int, ...]],
        warnings: Iterable[PublicationWarning] = (),
    ) -> None:
        canonical_document = canonical_json(document)
        object.__setattr__(self, "_canonical_document", canonical_document)
        object.__setattr__(
            self,
            "_flow_key_index",
            MappingProxyType(dict(flow_key_index)),
        )
        object.__setattr__(
            self,
            "content_hash",
            sha256(canonical_document).hexdigest(),
        )
        object.__setattr__(self, "warnings", tuple(warnings))

    @property
    def document(self) -> dict[str, JsonValue]:
        """Return a detached document copy for callers and persistence adapters."""

        return cast(dict[str, JsonValue], json.loads(self._canonical_document))

    @property
    def flow_key_index(self) -> dict[str, tuple[int, ...]]:
        """Return a detached flow-key index copy for callers."""

        return dict(self._flow_key_index)


def canonical_json(document: Mapping[str, JsonValue] | JsonValue) -> bytes:
    """Serialize JSON data reproducibly for immutable-version hashing."""

    try:
        serialized = json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise FlowPublicationError(
            "published definition is not canonical JSON"
        ) from exc
    return serialized.encode("utf-8")


def validate_for_publication(
    root: DiscussionFlow,
    context_schema: TemplateContextSchema,
    root_kind: RootKind,
) -> PublishedFlowDefinition:
    """Validate a recursive draft and return its immutable publication data."""

    if not isinstance(context_schema, TemplateContextSchema):
        raise FlowPublicationError("publication requires a template context schema")
    if not isinstance(root_kind, RootKind):
        raise FlowPublicationError("publication root kind is invalid")

    normalized_root = _normalize_root(root)
    _reject_event_only_cycles(normalized_root)
    index = _index_unique_keys(normalized_root)
    _validate_root(normalized_root, root_kind)
    warnings = _validate_nodes(normalized_root, context_schema)
    document = cast(dict[str, JsonValue], _model_dump(normalized_root, mode="json"))
    return PublishedFlowDefinition(
        document=document,
        flow_key_index=index,
        warnings=warnings,
    )


def _normalize_root(root: object) -> DiscussionFlow:
    try:
        return DiscussionFlow.model_validate(_model_dump(root))
    except (TypeError, ValidationError, ValueError) as exc:
        raise FlowPublicationError(
            "publication root must be an exact valid DiscussionFlow definition"
        ) from exc


def _index_unique_keys(root: DiscussionFlow) -> dict[str, tuple[int, ...]]:
    index: dict[str, tuple[int, ...]] = {}
    stack: list[tuple[DiscussionFlow, tuple[int, ...]]] = [(root, ())]
    while stack:
        node, path = stack.pop()
        if (
            not isinstance(node.key, str)
            or _STABLE_KEY_PATTERN.fullmatch(node.key) is None
        ):
            raise FlowPublicationError(f"flow key at {path!r} is invalid")
        if node.key in index:
            raise FlowPublicationError(f"duplicate flow key: {node.key}")
        index[node.key] = path
        for child_index in range(len(node.next_flows) - 1, -1, -1):
            child = node.next_flows[child_index]
            if not isinstance(child, DiscussionFlow):
                raise FlowPublicationError(f"flow {node.key} has an invalid child")
            stack.append((child, path + (child_index,)))
    return index


def _validate_root(root: DiscussionFlow, root_kind: RootKind) -> None:
    if root.trigger is not None:
        raise FlowPublicationError("published roots must have trigger=None")
    if (
        root_kind in {RootKind.SYSTEM, RootKind.SERVICE}
        and root.next_flow_mode is not NextFlowMode.CHECKPOINT
    ):
        raise FlowPublicationError("system and service roots must be checkpoints")


def _validate_nodes(
    root: DiscussionFlow, context_schema: TemplateContextSchema
) -> list[PublicationWarning]:
    warnings: list[PublicationWarning] = []
    stack: list[tuple[DiscussionFlow, bool]] = [(root, True)]
    while stack:
        node, is_root = stack.pop()
        _validate_node_shape(node, is_root=is_root)
        event_keys = _validate_action_list(node.actions, context_schema, node.key)
        event_keys.update(
            _validate_action_list(node.return_actions, context_schema, node.key)
        )
        _validate_direct_event_handlers(node, event_keys)
        if not node.actions and not node.next_flows:
            warnings.append(PublicationWarning(code="no_op_leaf", flow_key=node.key))
        for child in reversed(node.next_flows):
            stack.append((child, False))
    return warnings


def _validate_node_shape(node: DiscussionFlow, *, is_root: bool) -> None:
    if not isinstance(node.next_flow_mode, NextFlowMode):
        raise FlowPublicationError(f"flow {node.key} has an invalid next-flow mode")
    if not is_root and node.trigger is None:
        raise FlowPublicationError(f"non-root flow {node.key} requires a trigger")
    _validate_trigger(node.trigger, flow_key=node.key)
    if node.next_flow_mode is not NextFlowMode.CHECKPOINT and node.return_actions:
        raise FlowPublicationError(
            f"flow {node.key} may only declare return actions at a checkpoint"
        )
    if node.multi_intent_mode is MultiIntentMode.ANSWER:
        _validate_answer_flow(node)


def _validate_answer_flow(node: DiscussionFlow) -> None:
    """Allow answer fragments to emit presentations but never alter branch state."""

    answer_action_types = (
        SendMessageParaphrasedAction,
        SendMessageLlmAction,
        SendMessageFixedAction,
        SendButtonsAction,
        SendPhotoAction,
    )
    if node.next_flows:
        raise FlowPublicationError(f"answer flow {node.key} may not have children")
    if node.return_actions:
        raise FlowPublicationError(
            f"answer flow {node.key} may not have return actions"
        )
    if not all(isinstance(action, answer_action_types) for action in node.actions):
        raise FlowPublicationError(
            f"answer flow {node.key} contains a non-presentation action"
        )


def _validate_trigger(trigger: DiscussionTrigger | None, *, flow_key: str) -> None:
    if trigger is None:
        return
    try:
        parsed = parse_trigger(_model_dump(trigger))
    except (TypeError, ValidationError, ValueError) as exc:
        raise FlowPublicationError(f"flow {flow_key} has an invalid trigger") from exc
    if parsed != trigger:
        raise FlowPublicationError(f"flow {flow_key} trigger is not canonical")


def _validate_action_list(
    actions: list[DiscussionAction],
    context_schema: TemplateContextSchema,
    flow_key: str,
) -> set[str]:
    declared_event_keys: set[str] = set()
    for position, action in enumerate(actions):
        parsed = _parse_action_for_publication(action, flow_key)
        _validate_templates(_model_dump(parsed), context_schema, flow_key)
        action_event_keys = _action_event_keys(parsed, flow_key)
        if action_event_keys and position != len(actions) - 1:
            raise FlowPublicationError(
                f"event-emitting action in {flow_key} must be terminal"
            )
        declared_event_keys.update(action_event_keys)
    return declared_event_keys


def _parse_action_for_publication(
    action: DiscussionAction, flow_key: str
) -> DiscussionAction:
    try:
        parsed = parse_action(_model_dump(action))
    except (TypeError, ValidationError, ValueError) as exc:
        raise FlowPublicationError(f"flow {flow_key} has an invalid action") from exc
    if parsed != action:
        raise FlowPublicationError(f"flow {flow_key} action is not canonical")
    return parsed


def _model_dump(model: object, *, mode: str = "python") -> dict[str, Any]:
    if not hasattr(model, "model_dump"):
        raise TypeError("expected a Pydantic model")
    try:
        dumped = model.model_dump(
            mode=mode,
            warnings="error",
        )
    except (TypeError, ValueError) as exc:
        raise TypeError("Pydantic model contains invalid data") from exc
    if not isinstance(dumped, dict):
        raise TypeError("Pydantic model did not produce a mapping")
    return dumped


def _action_event_keys(action: DiscussionAction, flow_key: str) -> set[str]:
    event_keys = action.declared_event_keys
    for event_key in event_keys:
        if (
            not isinstance(event_key, str)
            or _STABLE_KEY_PATTERN.fullmatch(event_key) is None
        ):
            raise FlowPublicationError(f"flow {flow_key} declares an invalid event key")
        if event_key == "error":
            raise FlowPublicationError(
                "normal actions may not declare the reserved error event"
            )
    return set(event_keys)


def _validate_templates(
    value: object, context_schema: TemplateContextSchema, flow_key: str
) -> None:
    if isinstance(value, str):
        matches = list(TEMPLATE_TOKEN_PATTERN.finditer(value))
        without_templates = TEMPLATE_TOKEN_PATTERN.sub("", value)
        if "{{" in without_templates or "}}" in without_templates:
            raise FlowPublicationError(f"flow {flow_key} has a malformed template")
        for match in matches:
            name = match.group(1)
            if name not in context_schema.permitted_names:
                raise FlowPublicationError(
                    f"flow {flow_key} references undeclared template {name}"
                )
        return
    if isinstance(value, list):
        for item in value:
            _validate_templates(item, context_schema, flow_key)
    elif isinstance(value, dict):
        for item in value.values():
            _validate_templates(item, context_schema, flow_key)


def _validate_direct_event_handlers(node: DiscussionFlow, event_keys: set[str]) -> None:
    handlers: set[str] = set()
    for child in node.next_flows:
        trigger = child.trigger
        if isinstance(trigger, OnActionEventTrigger):
            event_key = trigger.event_key
            if event_key in handlers:
                raise FlowPublicationError(
                    f"flow {node.key} has duplicate direct handler for {event_key}"
                )
            handlers.add(event_key)
    missing = event_keys - handlers
    if missing:
        missing_key = min(missing)
        raise FlowPublicationError(
            f"flow {node.key} has no direct handler for {missing_key}"
        )
    unsolicited = handlers - event_keys - {"error"}
    if unsolicited:
        unsolicited_key = min(unsolicited)
        raise FlowPublicationError(
            f"flow {node.key} has unsolicited direct handler for {unsolicited_key}"
        )
    if sum(key == "error" for key in handlers) > 1:
        raise FlowPublicationError(
            f"flow {node.key} has multiple direct error handlers"
        )


def _reject_event_only_cycles(root: DiscussionFlow) -> None:
    nodes = _collect_nodes(root)
    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(node: DiscussionFlow) -> None:
        node_id = id(node)
        if node_id in visiting:
            raise FlowPublicationError("reachable event-only cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        for child in _reachable_direct_event_children(node):
            visit(child)
        visiting.remove(node_id)
        visited.add(node_id)

    for node in nodes:
        visit(node)


def _collect_nodes(root: DiscussionFlow) -> list[DiscussionFlow]:
    collected: list[DiscussionFlow] = []
    seen: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        node_id = id(node)
        if node_id in seen:
            continue
        seen.add(node_id)
        collected.append(node)
        for child in node.next_flows:
            if not isinstance(child, DiscussionFlow):
                raise FlowPublicationError(f"flow {node.key} has an invalid child")
            stack.append(child)
    return collected


def _reachable_direct_event_children(node: DiscussionFlow) -> list[DiscussionFlow]:
    declared = _declared_event_keys_without_validation(node.actions)
    declared.update(_declared_event_keys_without_validation(node.return_actions))
    children: list[DiscussionFlow] = []
    for child in node.next_flows:
        trigger = child.trigger
        if isinstance(trigger, OnActionEventTrigger) and (
            trigger.event_key in declared or trigger.event_key == "error"
        ):
            children.append(child)
    return children


def _declared_event_keys_without_validation(
    actions: list[DiscussionAction],
) -> set[str]:
    keys: set[str] = set()
    for action in actions:
        declared = getattr(action, "declared_event_keys", None)
        if isinstance(declared, frozenset):
            keys.update(declared)
    return keys
