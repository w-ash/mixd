"""Chat feedback domain entity.

A human thumbs-up/thumbs-down (with an optional free-text note) on a workflow
definition the chat assistant generated, recorded alongside the prompt that
produced it. Write-once: feedback rows are created and never mutated, so
there is no update path in the repository protocol — only ``save``.

This entity is human-only. It is recorded exclusively by the thumbs UI in
response to a person's judgment on a generated workflow; the assistant itself
never writes it. See ``application/use_cases/record_chat_feedback.py`` and the
tool registry's BLACKLISTED classification for that boundary.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid7

from attrs import define, field

from .shared import JsonValue

type FeedbackSignal = Literal["positive", "negative"]


@define(frozen=True, slots=True)
class ChatFeedback:
    """A single thumbs-up/thumbs-down judgment on an assistant-generated workflow."""

    user_id: str
    prompt: str
    generated_workflow_def: Mapping[str, JsonValue]
    signal: FeedbackSignal
    note: str | None = None
    created_at: datetime | None = None
    id: UUID = field(factory=uuid7)
