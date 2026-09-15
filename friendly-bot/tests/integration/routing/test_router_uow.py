"""R03's router keeps F01 state access behind its UoW repository boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Self
from uuid import UUID, uuid4

from friendly_bot.domain.flows import DiscussionFlow, NextFlowMode
from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.domain.state import OpenSelectionState
from friendly_bot.domain.triggers import MessageDiscussionFlowTrigger
from friendly_bot.persistence.repositories import PersonaCursorRecord
from friendly_bot.routing.contracts import MultiIntentTerminal
from friendly_bot.routing.router import ConstrainedRouter, IncomingText, RoutingTerminal

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
USER = uuid4()


class RecordingUow:
    def __init__(
        self, selection: OpenSelectionState, definition: dict[str, object]
    ) -> None:
        self.open_selections = SimpleNamespace(list_for_user=self._list_for_user)
        self.personas = SimpleNamespace(get_or_create=self._get_cursor)
        self.conversations = SimpleNamespace(list_after=self._list_after)
        self.flow_versions = SimpleNamespace(get=self._get_version)
        self.services = SimpleNamespace(get=self._get_service)
        self._selection = selection
        self._definition = definition
        self.calls: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def _list_for_user(
        self, user_id: UUID, *, now: datetime
    ) -> list[OpenSelectionState]:
        assert user_id == USER and now == NOW
        self.calls.append("selections")
        return [self._selection]

    async def _get_cursor(self, user_id: UUID) -> PersonaCursorRecord:
        assert user_id == USER
        self.calls.append("persona")
        return PersonaCursorRecord(USER, "", None, None)

    async def _list_after(self, user_id: UUID, cursor_id: UUID | None) -> list[object]:
        assert user_id == USER and cursor_id is None
        self.calls.append("conversation")
        return []

    async def _get_version(self, version_id: UUID) -> object:
        assert version_id == self._selection.flow_version_id
        self.calls.append("flow_version")
        return SimpleNamespace(definition=self._definition)

    async def _get_service(self, service_id: UUID) -> object:
        raise AssertionError(f"unexpected service lookup for {service_id}")


class TerminalGateway:
    async def route_and_plan(self, request: object) -> MultiIntentTerminal:
        return MultiIntentTerminal(kind="terminal", terminal="no_match")


async def test_router_reads_f01_uow_repositories_without_reply_identifier_state() -> (
    None
):
    """Native reply text reaches the prompt while no reply mapping is persisted."""

    version_id = uuid4()
    root = DiscussionFlow(
        key="system.home",
        next_flow_mode=NextFlowMode.CHECKPOINT,
        next_flows=[
            DiscussionFlow(
                key="flow.help",
                trigger=MessageDiscussionFlowTrigger(type="message", llm_gist="help"),
                next_flow_mode=NextFlowMode.ONE_AND_ONCE_ONLY,
            )
        ],
    )
    selection = OpenSelectionState(
        id=uuid4(),
        user_id=USER,
        flow_version_id=version_id,
        parent_flow_key="system.home",
        service_id=None,
        is_current=True,
        is_global_interruptive=False,
        ancestor_flow_keys=("system.home",),
        checkpoint_flow_keys=("system.home",),
        opened_at=NOW,
        last_focused_at=NOW,
    )
    definition = PublishedFlowDefinition(
        document=root.model_dump(mode="json"), flow_key_index={"system.home": ()}
    )
    uow = RecordingUow(selection, definition.document)

    result = await ConstrainedRouter(lambda: uow, TerminalGateway()).route_update(
        USER,
        IncomingText(body="help", replied_to_body="earlier words"),
        NOW,
    )

    assert result.terminal is RoutingTerminal.NO_MATCH
    assert uow.calls == ["selections", "persona", "conversation", "flow_version"]
