"""Server-side store for mutation actions awaiting user confirmation.

Two-phase protocol: mutation (``kind="write"``) tools propose an action (stored
here), the frontend renders a confirmation card, and the action executes only
when the user explicitly confirms. Actions expire after 5 minutes.

Durable since v0.9.5: the store is Postgres-backed because the propose and
confirm calls can land on *different* machines (Fly ``auto_start_machines``
plus the remote MCP transport) — an in-process dict would sever the two-phase
write guarantee exactly when a second machine spins up. Rows are short-lived
and evicted opportunistically on every ``create``.

This module owns the TTL and the error contract; the SQL lives in
``PendingActionRepository``. ``claim`` and ``cancel`` each run their delete and
their owner lookup inside one ``execute_use_case`` call, so the "was it
someone else's, or just gone?" question is answered against a single
consistent snapshot.
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from src.application.runner import execute_use_case
from src.domain.entities.pending_action import PendingAction
from src.domain.entities.shared import JsonDict
from src.domain.exceptions import ActionExpiredError, ForbiddenError
from src.domain.repositories.uow import UnitOfWorkProtocol

_TTL = timedelta(minutes=5)

__all__ = [
    "PendingAction",
    "PendingActionStore",
    "PostgresPendingActionStore",
    "pending_action_store",
]


class PendingActionStore(Protocol):
    """Contract shared by the durable store and the in-memory test double.

    ``claim`` and ``cancel`` validate the action against the creating user's
    id, so one user can never confirm or cancel another user's action:
    ``ActionExpiredError`` for a missing/expired action, ``ForbiddenError``
    for someone else's.
    """

    async def create(
        self,
        user_id: str,
        tool_name: str,
        tool_input: JsonDict,
        description: str,
        details: JsonDict,
    ) -> PendingAction: ...

    async def claim(self, action_id: UUID, user_id: str) -> PendingAction: ...

    async def cancel(self, action_id: UUID, user_id: str) -> None: ...


class PostgresPendingActionStore:
    """Durable pending-action store over the ``pending_actions`` table.

    Multi-machine-safe: ``claim`` is a single conditional
    ``DELETE … RETURNING``, so exactly one machine can win a confirmation
    even when two race on the same token.

    NO RLS on the table (``chat_feedback``/``schedules`` precedent): the
    store must distinguish "someone else's action" (``ForbiddenError``) from
    "expired" (``ActionExpiredError``), and RLS invisibility would collapse
    the two. Isolation is enforced by explicit ``user_id`` predicates in
    every query instead — which is also why these run with no ``user_id``
    on ``execute_use_case``.
    """

    async def create(
        self,
        user_id: str,
        tool_name: str,
        tool_input: JsonDict,
        description: str,
        details: JsonDict,
    ) -> PendingAction:
        now = datetime.now(UTC)
        return await execute_use_case(
            lambda uow: uow.get_pending_action_repository().insert_evicting_expired(
                user_id=user_id,
                tool_name=tool_name,
                tool_input=tool_input,
                description=description,
                details=details,
                created_at=now,
                cutoff=now - _TTL,
            )
        )

    async def claim(self, action_id: UUID, user_id: str) -> PendingAction:
        """Atomically retrieve-and-remove a pending action for execution.

        Raises ``ActionExpiredError`` if not found (expired or never existed)
        and ``ForbiddenError`` if the action belongs to a different user.
        """
        cutoff = datetime.now(UTC) - _TTL

        async def _claim(
            uow: UnitOfWorkProtocol,
        ) -> tuple[PendingAction | None, str | None]:
            repo = uow.get_pending_action_repository()
            claimed = await repo.claim_owned(action_id, user_id, cutoff)
            if claimed is not None:
                return claimed, None
            # Distinguish foreign from missing/expired for the error contract.
            return None, await repo.get_owner(action_id)

        action, owner = await execute_use_case(_claim)
        if action is not None:
            return action
        if owner is not None and owner != user_id:
            raise ForbiddenError("Cannot confirm another user's action")
        raise ActionExpiredError("This action has expired. Please try again.")

    async def cancel(self, action_id: UUID, user_id: str) -> None:
        """Remove a pending action without executing it (idempotent)."""

        async def _cancel(uow: UnitOfWorkProtocol) -> tuple[bool, str | None]:
            repo = uow.get_pending_action_repository()
            if await repo.delete_owned(action_id, user_id):
                return True, None
            return False, await repo.get_owner(action_id)

        deleted, owner = await execute_use_case(_cancel)
        if deleted:
            return
        if owner is not None and owner != user_id:
            raise ForbiddenError("Cannot cancel another user's action")
        # Already expired or cancelled — idempotent.


pending_action_store: PendingActionStore = PostgresPendingActionStore()
