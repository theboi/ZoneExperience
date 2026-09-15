"""Local safety policy for one-shot typed routing proposals."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from friendly_bot.routing.router import (
        MultiIntentRoutingResult,
        RoutedMatch,
    )


class MultiIntentPolicy:
    """Keep all safe answers while serializing interactive branch execution."""

    def partition(self, matches: tuple[RoutedMatch, ...]) -> MultiIntentRoutingResult:
        """Return answers, one interactive match, and ordered deferred candidates."""

        from friendly_bot.routing.router import MultiIntentRoutingResult, RoutingError

        seen_keys: set[str] = set()
        safety: RoutedMatch | None = None
        answers: list[RoutedMatch] = []
        interactive: list[RoutedMatch] = []
        for match in matches:
            key = match.candidate.key
            if key in seen_keys:
                raise RoutingError("routing result contains a duplicate flow key")
            seen_keys.add(key)
            if _is_interruptive_safety(match):
                safety = match
                continue
            if match.candidate.multi_intent_mode == "answer":
                answers.append(match)
            else:
                interactive.append(match)
        if safety is not None:
            return MultiIntentRoutingResult((), safety, (), None)
        return MultiIntentRoutingResult(
            answers=tuple(answers),
            interactive=interactive[0] if interactive else None,
            deferred=tuple(match.candidate for match in interactive[1:]),
            terminal=None,
        )


def _is_interruptive_safety(match: RoutedMatch) -> bool:
    return (
        match.candidate.is_global_interruptive
        and match.candidate.key == "system.global.safety"
    )
