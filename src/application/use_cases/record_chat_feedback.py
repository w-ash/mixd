"""Record a human thumbs-up/thumbs-down on an assistant-generated workflow.

Human-only: this use case is invoked exclusively by the thumbs UI in response
to a person's judgment on a workflow the chat assistant generated. It is
classified BLACKLISTED in the tool registry (``src/application/tools/registry.py``)
so the assistant can never call it on itself — feedback must reflect an
independent human signal, not the model grading its own output.
"""

from collections.abc import Mapping
from uuid import UUID

from attrs import define

from src.domain.entities.chat_feedback import ChatFeedback, FeedbackSignal
from src.domain.entities.shared import JsonValue
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True)
class RecordChatFeedbackCommand:
    user_id: str
    prompt: str
    generated_workflow_def: Mapping[str, JsonValue]
    signal: FeedbackSignal
    note: str | None = None


@define(frozen=True)
class RecordChatFeedbackResult:
    feedback_id: UUID


class RecordChatFeedbackUseCase:
    """Persist one thumbs-up/thumbs-down judgment on a generated workflow."""

    async def execute(
        self,
        command: RecordChatFeedbackCommand,
        uow: UnitOfWorkProtocol,
    ) -> RecordChatFeedbackResult:
        async with uow:
            feedback = ChatFeedback(
                user_id=command.user_id,
                prompt=command.prompt,
                generated_workflow_def=command.generated_workflow_def,
                signal=command.signal,
                note=command.note,
            )
            saved = await uow.get_chat_feedback_repository().save(feedback)
            await uow.commit()
            return RecordChatFeedbackResult(feedback_id=saved.id)
