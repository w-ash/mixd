"""In-memory ``PendingActionStore`` double for unit tests.

Preserves the exact semantics the production ``PostgresPendingActionStore``
implements (5-minute TTL, single-use claim, owner checks, idempotent cancel)
without a database, so dispatcher/executor unit tests can swap it in via
``monkeypatch.setattr(_common, "pending_action_store", store)``.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from src.application.chat.pending_actions import PendingAction
from src.domain.entities.shared import JsonDict
from src.domain.exceptions import ActionExpiredError, ForbiddenError

_TTL = timedelta(minutes=5)


class InMemoryPendingActionStore:
    """Dict-backed store matching the ``PendingActionStore`` protocol."""

    def __init__(self) -> None:
        self._actions: dict[UUID, PendingAction] = {}

    async def create(
        self,
        user_id: str,
        tool_name: str,
        tool_input: JsonDict,
        description: str,
        details: JsonDict,
    ) -> PendingAction:
        self._evict_expired()
        action = PendingAction(
            action_id=uuid4(),
            user_id=user_id,
            tool_name=tool_name,
            tool_input=dict(tool_input),
            description=description,
            details=details,
            created_at=datetime.now(UTC),
        )
        self._actions[action.action_id] = action
        return action

    async def claim(self, action_id: UUID, user_id: str) -> PendingAction:
        self._evict_expired()
        action = self._actions.get(action_id)
        if action is None:
            raise ActionExpiredError("This action has expired. Please try again.")
        if action.user_id != user_id:
            raise ForbiddenError("Cannot confirm another user's action")
        del self._actions[action_id]
        return action

    async def cancel(self, action_id: UUID, user_id: str) -> None:
        self._evict_expired()
        action = self._actions.get(action_id)
        if action is None:
            return  # Already expired or cancelled — idempotent
        if action.user_id != user_id:
            raise ForbiddenError("Cannot cancel another user's action")
        del self._actions[action_id]

    def _evict_expired(self) -> None:
        cutoff = datetime.now(UTC) - _TTL
        expired = [aid for aid, a in self._actions.items() if a.created_at < cutoff]
        for aid in expired:
            del self._actions[aid]
