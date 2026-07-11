"""Chat feedback repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable
from typing import Protocol

from src.domain.entities.chat_feedback import ChatFeedback


class ChatFeedbackRepositoryProtocol(Protocol):
    """Repository interface for ``ChatFeedback`` persistence.

    Write-once: feedback is recorded and never updated, so ``save`` is the
    only method. The ``chat_feedback`` table has NO RLS policy — like
    ``schedules`` — so per-user isolation for any future read path must filter
    by ``user_id`` explicitly rather than relying on a database policy.
    """

    def save(self, feedback: ChatFeedback) -> Awaitable[ChatFeedback]:
        """Insert a new feedback row and return it as persisted."""
        ...
