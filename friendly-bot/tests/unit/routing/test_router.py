"""Constrained configured-key router behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Self, cast
from uuid import UUID, uuid4

import pytest

from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode
from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.domain.state import OpenSelectionState
from friendly_bot.domain.triggers import (
    OnAnyOfTrigger,
    OnButtonPressTrigger,
    OnMessageTrigger,
)
from friendly_bot.persistence.repositories import (
    ConversationMessageRecord,
    PersonaCursorRecord,
)
from friendly_bot.persistence.uow import UnitOfWork
from friendly_bot.routing.contracts import (
    MultiIntentMatches,
    MultiIntentTerminal,
    PlannedFlowMatch,
)
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
                trigger=OnMessageTrigger(type="message", llm_gist=gist),
                next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
            ),
            DiscussionFlow(
                key=f"{child_key}.button",
                trigger=OnButtonPressTrigger(type="button", button_id="button.one"),
                next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
            ),
        ],
    )
    return PublishedFlowDefinition(
        document=root.model_dump(mode="json"),
        flow_key_index={root_key: (), child_key: (0,)},
    )


def _selection(
    version_id: UUID, parent_key: str, *, current: bool
) -> OpenSelectionState:
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
    answers: list[MultiIntentMatches | MultiIntentTerminal]
    requests: list[object]

    def __init__(self, answers: list[MultiIntentMatches | MultiIntentTerminal]) -> None:
        self.answers = answers
        self.requests = []

    async def route_and_plan(
        self, request: object
    ) -> MultiIntentMatches | MultiIntentTerminal:
        self.requests.append(request)
        return self.answers.pop(0)


def _matches(*flow_ids: str) -> MultiIntentMatches:
    return MultiIntentMatches(
        kind="matches",
        matches=tuple(PlannedFlowMatch(flow_id=flow_id) for flow_id in flow_ids),
    )


class FakeUow:
    def __init__(
        self,
        selections: list[OpenSelectionState],
        definitions: dict[UUID, object],
        *,
        messages: list[ConversationMessageRecord] | None = None,
    ) -> None:
        self.open_selections = SimpleNamespace(list_for_user=self._list_selections)
        self.personas = SimpleNamespace(get_or_create=self._get_cursor)
        self.conversations = SimpleNamespace(list_after=self._list_messages)
        self.flow_versions = SimpleNamespace(get=self._get_definition)
        self.services = SimpleNamespace(get=self._get_service)
        self._selections = selections
        self._definitions = definitions
        self._messages = messages or []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def _list_selections(
        self, user_id: UUID, *, now: datetime
    ) -> list[OpenSelectionState]:
        assert user_id == USER and now == NOW
        return self._selections

    async def _get_cursor(self, user_id: UUID) -> PersonaCursorRecord:
        assert user_id == USER
        return PersonaCursorRecord(USER, "steady", None, None)

    async def _list_messages(
        self, user_id: UUID, cursor_id: UUID | None
    ) -> list[object]:
        assert user_id == USER and cursor_id is None
        return self._messages

    async def _get_definition(self, version_id: UUID) -> object:
        return self._definitions[version_id]

    async def _get_service(self, service_id: UUID) -> object:
        raise AssertionError(f"unexpected service lookup for {service_id}")


def test_candidate_assembly_keeps_current_and_reusable_message_choices_together() -> (
    None
):
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
            reusable_version: _definition(
                "system.reusable", "flow.reusable", gist="later"
            ),
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


def test_candidate_assembly_includes_one_any_of_message_candidate() -> None:
    version = uuid4()
    root = DiscussionFlow(
        key="system.home",
        next_flow_mode=NextFlowMode.CHECKPOINT,
        next_flows=[
            DiscussionFlow(
                key="system.home.directions",
                trigger=OnAnyOfTrigger(
                    type="any_of",
                    triggers=[
                        OnButtonPressTrigger(
                            type="button", button_id="menu.directions"
                        ),
                        OnMessageTrigger(
                            type="message", llm_gist="asks for directions"
                        ),
                    ],
                ),
                next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
            )
        ],
    )

    candidates = CandidateAssembler().assemble(
        [_selection(version, "system.home", current=True)],
        {
            version: PublishedFlowDefinition(
                document=root.model_dump(mode="json"),
                flow_key_index={},
            )
        },
        now=NOW,
    )

    assert [(candidate.key, candidate.gists) for candidate in candidates] == [
        ("system.home.directions", ("asks for directions",))
    ]


async def test_router_routes_all_matches_with_one_model_operation() -> None:
    """One typed update must return all matches without a system.done call."""

    current_version = uuid4()
    global_version = uuid4()
    gateway = StubGateway([_matches("flow.current", "flow.global")])
    uow = FakeUow(
        [
            _selection(current_version, "system.current", current=True),
            _selection(global_version, "system.global", current=False),
        ],
        {
            current_version: SimpleNamespace(
                definition=_definition(
                    "system.current", "flow.current", gist="now"
                ).document
            ),
            global_version: SimpleNamespace(
                definition=_definition(
                    "system.global", "flow.global", gist="always"
                ).document
            ),
        },
    )
    router = ConstrainedRouter(lambda: uow, gateway)

    result = await router.route_update(
        USER,
        IncomingText(body="help", replied_to_body="old question"),
        NOW,
    )

    assert result.interactive is not None
    assert result.interactive.candidate.key == "flow.current"
    assert tuple(candidate.key for candidate in result.deferred) == ("flow.global",)
    assert result.terminal is None
    assert len(gateway.requests) == 1
    assert gateway.requests[0].reply_body == "old question"
    assert tuple(candidate.flow_id for candidate in gateway.requests[0].candidates) == (
        "flow.current",
        "flow.global",
    )


async def test_ambiguous_and_no_match_are_terminals() -> None:
    """The one-shot model exposes only no-match and clarify terminals."""

    version = uuid4()
    definition = SimpleNamespace(
        definition=_definition("system.current", "flow.current", gist="now").document
    )
    selections = [_selection(version, "system.current", current=True)]

    clarify = await ConstrainedRouter(
        lambda: FakeUow(selections, {version: definition}),
        StubGateway(
            [MultiIntentTerminal(kind="terminal", terminal="clarify_ambiguous_context")]
        ),
    ).route_update(USER, IncomingText(body="which one"), NOW)
    no_match = await ConstrainedRouter(
        lambda: FakeUow(selections, {version: definition}),
        StubGateway([MultiIntentTerminal(kind="terminal", terminal="no_match")]),
    ).route_update(USER, IncomingText(body="unknown"), NOW)

    assert clarify.terminal is RoutingTerminal.CLARIFY
    assert no_match.terminal is RoutingTerminal.NO_MATCH


async def test_router_can_use_the_caller_owned_ingress_unit_of_work() -> None:
    """I04 dispatch must not open a nested transaction while routing normal text."""

    version = uuid4()
    uow = FakeUow(
        [_selection(version, "system.current", current=True)],
        {
            version: SimpleNamespace(
                definition=_definition(
                    "system.current", "flow.current", gist="now"
                ).document
            )
        },
    )
    router = ConstrainedRouter(
        lambda: cast(UnitOfWork, uow),
        StubGateway([MultiIntentTerminal(kind="terminal", terminal="no_match")]),
    )

    result = await router.route_update_in_uow(
        cast(UnitOfWork, uow),
        user_id=USER,
        incoming=IncomingText(body="help"),
        now=NOW,
    )

    assert result.terminal is RoutingTerminal.NO_MATCH


async def test_router_uses_only_the_current_input_when_history_is_persisted() -> None:
    """A prior question must never cause its answer flow to run again."""

    version = uuid4()
    gateway = StubGateway([MultiIntentTerminal(kind="terminal", terminal="no_match")])
    uow = FakeUow(
        [_selection(version, "system.current", current=True)],
        {
            version: SimpleNamespace(
                definition=_definition(
                    "system.current", "flow.current", gist="now"
                ).document
            )
        },
        messages=[
            ConversationMessageRecord(
                id=uuid4(),
                user_id=USER,
                source_kind="telegram",
                source_message_id=1,
                body="where are the directions?",
                replied_to_body=None,
                occurred_at=NOW,
            ),
            ConversationMessageRecord(
                id=uuid4(),
                user_id=USER,
                source_kind="telegram",
                source_message_id=2,
                body="what is The Zone?",
                replied_to_body=None,
                occurred_at=NOW,
            ),
        ],
    )

    await ConstrainedRouter(lambda: cast(UnitOfWork, uow), gateway).route_update_in_uow(
        cast(UnitOfWork, uow),
        user_id=USER,
        incoming=IncomingText(body="what is The Zone?"),
        now=NOW,
    )

    assert gateway.requests[0].messages == ("what is The Zone?",)
