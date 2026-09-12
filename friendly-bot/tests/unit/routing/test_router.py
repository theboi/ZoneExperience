"""Constrained configured-key router behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Self
from uuid import UUID, uuid4

import pytest

from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode
from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.domain.state import OpenSelectionState
from friendly_bot.domain.triggers import (
    ButtonDiscussionFlowTrigger,
    MessageDiscussionFlowTrigger,
)
from friendly_bot.persistence.repositories import PersonaCursorRecord
from friendly_bot.routing.router import (
    CandidateAssembler,
    ConstrainedRouter,
    DuplicateRoutingCandidateError,
    IncomingText,
    RoutingTerminal,
)

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
USER = uuid4()


def _definition(root_key: str, child_key: str, *, gist: str) -> PublishedFlowDefinition:
    root = DiscussionFlow(
        key=root_key,
        next_flow_mode=NextFlowMode.CHECKPOINT,
        next_flows=[
            DiscussionFlow(
                key=child_key,
                trigger=MessageDiscussionFlowTrigger(type="message", llm_gist=gist),
                next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
            ),
            DiscussionFlow(
                key=f"{child_key}.button",
                trigger=ButtonDiscussionFlowTrigger(type="button", button_id="button.one"),
                next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
            ),
        ],
    )
    return PublishedFlowDefinition(
        document=root.model_dump(mode="json"),
        flow_key_index={root_key: (), child_key: (0,)},
    )


def _selection(version_id: UUID, parent_key: str, *, current: bool) -> OpenSelectionState:
    return OpenSelectionState(
        id=uuid4(),
        user_id=USER,
        flow_version_id=version_id,
        parent_flow_key=parent_key,
        service_id=None,
        is_current=current,
        is_global_interruptive=False,
        ancestor_flow_keys=(parent_key,),
        checkpoint_flow_keys=(parent_key,),
        opened_at=NOW,
        last_focused_at=NOW,
    )


@dataclass
class StubGateway:
    answers: list[str]
    requests: list[object]

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.requests = []

    async def select_key(self, request: object) -> str:
        self.requests.append(request)
        return self.answers.pop(0)


class FakeUow:
    def __init__(self, selections: list[OpenSelectionState], definitions: dict[UUID, object]) -> None:
        self.open_selections = SimpleNamespace(list_for_user=self._list_selections)
        self.personas = SimpleNamespace(get_or_create=self._get_cursor)
        self.conversations = SimpleNamespace(list_after=self._list_messages)
        self.flow_versions = SimpleNamespace(get=self._get_definition)
        self.services = SimpleNamespace(get=self._get_service)
        self._selections = selections
        self._definitions = definitions

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def _list_selections(self, user_id: UUID, *, now: datetime) -> list[OpenSelectionState]:
        assert user_id == USER and now == NOW
        return self._selections

    async def _get_cursor(self, user_id: UUID) -> PersonaCursorRecord:
        assert user_id == USER
        return PersonaCursorRecord(USER, "steady", None, None)

    async def _list_messages(self, user_id: UUID, cursor_id: UUID | None) -> list[object]:
        assert user_id == USER and cursor_id is None
        return []

    async def _get_definition(self, version_id: UUID) -> object:
        return self._definitions[version_id]

    async def _get_service(self, service_id: UUID) -> object:
        raise AssertionError(f"unexpected service lookup for {service_id}")


def test_candidate_assembly_keeps_current_and_reusable_message_choices_together() -> None:
    """Making current scope exclusive would hide a reusable configured action."""

    current_version = uuid4()
    reusable_version = uuid4()
    candidates = CandidateAssembler().assemble(
        [
            _selection(current_version, "system.current", current=True),
            _selection(reusable_version, "system.reusable", current=False),
        ],
        {
            current_version: _definition("system.current", "flow.current", gist="now"),
            reusable_version: _definition("system.reusable", "flow.reusable", gist="later"),
        },
        now=NOW,
    )

    assert [(candidate.key, candidate.source) for candidate in candidates] == [
        ("flow.current", "current"),
        ("flow.reusable", "reusable"),
    ]


def test_candidate_assembly_rejects_duplicate_configured_keys() -> None:
    """Silently preferring one published flow would make routing nondeterministic."""

    first = uuid4()
    second = uuid4()
    with pytest.raises(DuplicateRoutingCandidateError):
        CandidateAssembler().assemble(
            [
                _selection(first, "system.first", current=True),
                _selection(second, "system.second", current=False),
            ],
            {
                first: _definition("system.first", "flow.same", gist="first"),
                second: _definition("system.second", "flow.same", gist="second"),
            },
            now=NOW,
        )


async def test_router_selects_two_distinct_keys_then_done() -> None:
    """A selected key must disappear before the next model decision."""

    current_version = uuid4()
    global_version = uuid4()
    gateway = StubGateway(["flow.current", "flow.global", "system.done"])
    uow = FakeUow(
        [
            _selection(current_version, "system.current", current=True),
            _selection(global_version, "system.global", current=False),
        ],
        {
            current_version: SimpleNamespace(
                definition=_definition("system.current", "flow.current", gist="now").document
            ),
            global_version: SimpleNamespace(
                definition=_definition("system.global", "flow.global", gist="always").document
            ),
        },
    )
    router = ConstrainedRouter(lambda: uow, gateway)

    result = await router.route_update(
        USER,
        IncomingText(body="help", replied_to_body="old question"),
        NOW,
    )

    assert [decision.key for decision in result.selected_keys] == [
        "flow.current",
        "flow.global",
    ]
    assert result.terminal is RoutingTerminal.DONE
    assert gateway.requests[0].reply_body == "old question"
    assert gateway.requests[0].allowed_keys == {
        "flow.current",
        "flow.global",
        "system.done",
        "system.no_match",
        "system.clarify_ambiguous_context",
    }
    assert "flow.current" not in gateway.requests[1].allowed_keys


async def test_ambiguous_and_no_match_are_terminals() -> None:
    """Only the three reserved terminal keys may stop an update."""

    version = uuid4()
    definition = SimpleNamespace(
        definition=_definition("system.current", "flow.current", gist="now").document
    )
    selections = [_selection(version, "system.current", current=True)]

    clarify = await ConstrainedRouter(
        lambda: FakeUow(selections, {version: definition}),
        StubGateway(["system.clarify_ambiguous_context"]),
    ).route_update(USER, IncomingText(body="which one"), NOW)
    no_match = await ConstrainedRouter(
        lambda: FakeUow(selections, {version: definition}),
        StubGateway(["system.no_match"]),
    ).route_update(USER, IncomingText(body="unknown"), NOW)

    assert clarify.terminal is RoutingTerminal.CLARIFY
    assert no_match.terminal is RoutingTerminal.NO_MATCH
