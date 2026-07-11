"""Server-side store for mutation actions awaiting user confirmation.

Two-phase protocol: mutation (``kind="write"``) tools propose an action (stored
here), the frontend renders a confirmation card, and the action executes only
when the user explicitly confirms. Actions expire after 5 minutes. Ephemeral
and in-process, matching mixd's deployment shape and the ephemeral-conversation
decision — no DB persistence.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from attrs import define

from src.domain.entities.shared import JsonDict
from src.domain.exceptions import ActionExpiredError, ForbiddenError

_TTL = timedelta(minutes=5)


@define(frozen=True, slots=True)
class PendingAction:
    """A proposed mutation held until the acting user confirms it."""

    action_id: UUID
    user_id: str
    tool_name: str
    tool_input: JsonDict
    description: str
    details: JsonDict
    created_at: datetime


class PendingActionStore:
    """In-memory store for pending mutation confirmations.

    Safe for a single-process async app (no concurrent writers). Actions are
    keyed by UUID and validated against the creating user's id on claim/cancel,
    so one user can never confirm or cancel another user's action.
    """

    def __init__(self) -> None:
        self._actions: dict[UUID, PendingAction] = {}

    def create(
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
            tool_input=tool_input,
            description=description,
            details=details,
            created_at=datetime.now(UTC),
        )
        self._actions[action.action_id] = action
        return action

    def claim(self, action_id: UUID, user_id: str) -> PendingAction:
        """Retrieve and remove a pending action for execution.

        Raises ``ActionExpiredError`` if not found (expired or never existed)
        and ``ForbiddenError`` if the action belongs to a different user.
        """
        self._evict_expired()
        action = self._actions.get(action_id)
        if action is None:
            raise ActionExpiredError("This action has expired. Please try again.")
        if action.user_id != user_id:
            raise ForbiddenError("Cannot confirm another user's action")
        del self._actions[action_id]
        return action

    def cancel(self, action_id: UUID, user_id: str) -> None:
        """Remove a pending action without executing it (idempotent)."""
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


pending_action_store = PendingActionStore()
