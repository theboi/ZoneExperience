"""Validated, declarative discussion-flow configuration contracts."""

from friendly_bot.domain.actions import DiscussionAction, parse_action
from friendly_bot.domain.events import ActionEvent
from friendly_bot.domain.triggers import DiscussionTrigger, parse_trigger

__all__ = [
    "ActionEvent",
    "DiscussionAction",
    "DiscussionTrigger",
    "parse_action",
    "parse_trigger",
]
