"""Unit tests for RecordChatFeedbackUseCase.

Tests use a mock UoW to verify the use case builds a ``ChatFeedback`` entity
from the command, saves it via the repository, and commits the transaction.
"""

import pytest

from src.application.use_cases.record_chat_feedback import (
    RecordChatFeedbackCommand,
    RecordChatFeedbackResult,
    RecordChatFeedbackUseCase,
)
from src.domain.entities.chat_feedback import ChatFeedback
from tests.fixtures import make_mock_uow


def _cmd(**overrides) -> RecordChatFeedbackCommand:
    defaults = {
        "user_id": "default",
        "prompt": "make me a chill playlist",
        "generated_workflow_def": {"nodes": [{"type": "source.playlist"}]},
        "signal": "positive",
        "note": None,
    }
    defaults.update(overrides)
    return RecordChatFeedbackCommand(**defaults)


class TestRecordChatFeedbackHappyPath:
    """Successful feedback recording."""

    async def test_saves_entity_with_command_fields(self) -> None:
        uow = make_mock_uow()
        feedback_repo = uow.get_chat_feedback_repository()

        async def _save(feedback: ChatFeedback) -> ChatFeedback:
            return feedback

        feedback_repo.save.side_effect = _save

        command = _cmd(note=None)
        result = await RecordChatFeedbackUseCase().execute(command, uow)

        feedback_repo.save.assert_awaited_once()
        saved = feedback_repo.save.call_args[0][0]
        assert isinstance(saved, ChatFeedback)
        assert saved.user_id == command.user_id
        assert saved.prompt == command.prompt
        assert saved.generated_workflow_def == command.generated_workflow_def
        assert saved.signal == command.signal
        assert saved.note is None

        assert isinstance(result, RecordChatFeedbackResult)
        assert result.feedback_id == saved.id

    async def test_commits_transaction(self) -> None:
        uow = make_mock_uow()
        feedback_repo = uow.get_chat_feedback_repository()

        async def _save(feedback: ChatFeedback) -> ChatFeedback:
            return feedback

        feedback_repo.save.side_effect = _save

        await RecordChatFeedbackUseCase().execute(_cmd(), uow)

        uow.commit.assert_awaited_once()

    async def test_note_is_passed_through_when_present(self) -> None:
        uow = make_mock_uow()
        feedback_repo = uow.get_chat_feedback_repository()

        async def _save(feedback: ChatFeedback) -> ChatFeedback:
            return feedback

        feedback_repo.save.side_effect = _save

        command = _cmd(signal="negative", note="wrong genre filter")
        await RecordChatFeedbackUseCase().execute(command, uow)

        saved = feedback_repo.save.call_args[0][0]
        assert saved.signal == "negative"
        assert saved.note == "wrong genre filter"


class TestRecordChatFeedbackErrors:
    """Failure propagation — the use case does not swallow repository errors."""

    async def test_repository_error_propagates_and_rolls_back(self) -> None:
        uow = make_mock_uow()
        feedback_repo = uow.get_chat_feedback_repository()
        feedback_repo.save.side_effect = RuntimeError("db unavailable")

        with pytest.raises(RuntimeError):
            await RecordChatFeedbackUseCase().execute(_cmd(), uow)

        uow.rollback.assert_awaited_once()
        uow.commit.assert_not_awaited()
