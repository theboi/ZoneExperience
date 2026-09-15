"""Local policy for one-shot typed routing matches."""

from __future__ import annotations

import pytest

from friendly_bot.responses.planner import CandidateResponsePlan, ReplyPlan
from friendly_bot.routing.policy import MultiIntentPolicy
from friendly_bot.routing.router import RoutedMatch, RoutingCandidate, RoutingError


def _match(key: str, *, mode: str, safety: bool = False) -> RoutedMatch:
    return RoutedMatch(
        candidate=RoutingCandidate(
            key=key,
            gists=("configured gist",),
            source="current",
            is_current=True,
            service_id=None,
            is_global_interruptive=safety,
            multi_intent_mode=mode,
            response_plan=CandidateResponsePlan((), ()),
        ),
        reply_plan=ReplyPlan(()),
    )


@pytest.mark.parametrize(
    ("matches", "answers", "interactive", "deferred"),
    [
        (
            (_match("answer.one", mode="answer"), _match("answer.two", mode="answer")),
            ("answer.one", "answer.two"),
            None,
            (),
        ),
        (
            (
                _match("answer.one", mode="answer"),
                _match("interactive.one", mode="interactive"),
            ),
            ("answer.one",),
            "interactive.one",
            (),
        ),
        (
            (
                _match("answer.one", mode="answer"),
                _match("interactive.one", mode="interactive"),
                _match("interactive.two", mode="interactive"),
                _match("interactive.three", mode="interactive"),
            ),
            ("answer.one",),
            "interactive.one",
            ("interactive.two", "interactive.three"),
        ),
    ],
)
def test_policy_keeps_all_answers_but_only_one_interactive_match(
    matches: tuple[RoutedMatch, ...],
    answers: tuple[str, ...],
    interactive: str | None,
    deferred: tuple[str, ...],
) -> None:
    result = MultiIntentPolicy().partition(matches)

    assert tuple(match.candidate.key for match in result.answers) == answers
    assert (
        result.interactive.candidate.key if result.interactive is not None else None
    ) == interactive
    assert tuple(candidate.key for candidate in result.deferred) == deferred


def test_policy_gives_an_interruptive_safety_match_exclusive_priority() -> None:
    result = MultiIntentPolicy().partition(
        (
            _match("answer.one", mode="answer"),
            _match("system.global.safety", mode="interactive", safety=True),
            _match("interactive.one", mode="interactive"),
        )
    )

    assert result.answers == ()
    assert result.interactive is not None
    assert result.interactive.candidate.key == "system.global.safety"
    assert result.deferred == ()


def test_policy_rejects_a_duplicate_selected_key() -> None:
    with pytest.raises(RoutingError, match="duplicate"):
        MultiIntentPolicy().partition(
            (_match("answer.one", mode="answer"), _match("answer.one", mode="answer"))
        )
