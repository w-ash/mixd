"""Request schemas for the chat endpoint."""

from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.config.settings import EffortLevel
from src.domain.entities.shared import JsonValue

_MAX_CONTENT_PER_MESSAGE = 20_480  # 20 KB
_MAX_TOTAL_CONTENT = 102_400  # 100 KB
_MAX_MESSAGES = 50


class ChatMessageInput(BaseModel):
    """One conversation turn from the client."""

    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=_MAX_CONTENT_PER_MESSAGE)


class ConfirmationInput(BaseModel):
    """A user's approve/cancel decision on a proposed mutation."""

    action_id: str
    approved: bool


class ChatRequest(BaseModel):
    """A chat request: message history plus optional confirmation and effort."""

    messages: list[ChatMessageInput] = Field(..., max_length=_MAX_MESSAGES)
    confirmation: ConfirmationInput | None = None
    # The browser's local calendar date, so "this month" / "last 6 months"
    # resolve to the user's clock, not UTC. Falls back to server UTC when absent.
    client_date: date | None = None
    # Per-request effort override (the UI control lands in v0.9.2). Falls back
    # to ChatConfig.effort.
    effort: EffortLevel | None = None
    # The workflow open in the editor (route `/workflows/:id/edit`), so the
    # server can inject its context into the system prompt. None off that route.
    current_workflow_id: UUID | None = None
    # The coarse UI section the user is on (e.g. "playlists", "library"), used
    # for rule-based tool routing: tools relevant to the section load eagerly
    # while the rest stay behind tool-search. Unknown/absent → no promotion.
    page: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_total_content_size(self) -> Self:
        total = sum(len(m.content) for m in self.messages)
        if total > _MAX_TOTAL_CONTENT:
            msg = (
                f"Total message content ({total} bytes) exceeds the "
                f"{_MAX_TOTAL_CONTENT}-byte limit"
            )
            raise ValueError(msg)
        return self


_MAX_FEEDBACK_NOTE = 2_000


class ChatFeedbackRequest(BaseModel):
    """Thumbs up/down on a generated workflow, with the triggering prompt."""

    prompt: str = Field(..., max_length=_MAX_CONTENT_PER_MESSAGE)
    generated_workflow_def: dict[str, JsonValue]
    signal: Literal["positive", "negative"]
    note: str | None = Field(default=None, max_length=_MAX_FEEDBACK_NOTE)


class ChatFeedbackResponse(BaseModel):
    """The persisted feedback row's id."""

    id: UUID
