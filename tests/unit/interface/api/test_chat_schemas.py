"""Unit tests for chat request-schema validators.

Covers the message-count cap wired to ``ChatConfig.max_messages`` (R5) and the
size bound on ``ChatFeedbackRequest.generated_workflow_def`` (X1).
"""

import pytest

from src.interface.api.schemas.chat import ChatFeedbackRequest, ChatRequest


def _msg(content: str = "hi") -> dict[str, str]:
    return {"role": "user", "content": content}


class TestMessageCountCap:
    def test_accepts_up_to_configured_max(self) -> None:
        from src.config.settings import settings

        limit = settings.chat.max_messages
        req = ChatRequest.model_validate({"messages": [_msg() for _ in range(limit)]})
        assert len(req.messages) == limit

    def test_rejects_over_configured_max(self) -> None:
        from src.config.settings import settings

        over = settings.chat.max_messages + 1
        with pytest.raises(ValueError, match="Too many messages"):
            ChatRequest.model_validate({"messages": [_msg() for _ in range(over)]})


class TestFeedbackWorkflowDefSize:
    def test_accepts_small_def(self) -> None:
        req = ChatFeedbackRequest.model_validate({
            "prompt": "make me a playlist",
            "generated_workflow_def": {"nodes": []},
            "signal": "positive",
        })
        assert req.signal == "positive"

    def test_rejects_oversized_def(self) -> None:
        # >100 KB once serialized.
        huge = {"blob": "x" * 200_000}
        with pytest.raises(ValueError, match="exceeds"):
            ChatFeedbackRequest.model_validate({
                "prompt": "make me a playlist",
                "generated_workflow_def": huge,
                "signal": "negative",
            })
