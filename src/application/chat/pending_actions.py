"""Server-side store for mutation actions awaiting user confirmation.

Two-phase protocol: mutation (``kind="write"``) tools propose an action (stored
here), the frontend renders a confirmation card, and the action executes only
when the user explicitly confirms. Actions expire after 5 minutes.

Durable since v0.9.5: the store is Postgres-backed because the propose and
confirm calls can land on *different* machines (Fly ``auto_start_machines``
plus the remote MCP transport) — an in-process dict would sever the two-phase
write guarantee exactly when a second machine spins up. Rows are short-lived
and evicted opportunistically on every ``create``.

Layer note: this application-layer module imports infrastructure
(session/model) function-scoped only — the sanctioned ``runner.py`` bridge
pattern — so the layer edge stays narrow and import-time clean.
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import UUID

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
    every query instead.
    """

    async def create(
        self,
        user_id: str,
        tool_name: str,
        tool_input: JsonDict,
        description: str,
        details: JsonDict,
    ) -> PendingAction:
        from sqlalchemy import delete

        from src.infrastructure.persistence.database.db_connection import get_session
        from src.infrastructure.persistence.database.db_models import DBPendingAction

        now = datetime.now(UTC)
        row = DBPendingAction(
            user_id=user_id,
            tool_name=tool_name,
            tool_input=dict(tool_input),
            description=description,
            details=details,
            created_at=now,
        )
        async with get_session() as session:
            # Opportunistic eviction replaces the in-memory _evict_expired().
            await session.execute(
                delete(DBPendingAction).where(DBPendingAction.created_at < now - _TTL)
            )
            session.add(row)
            await session.flush()
            action_id = row.id
        return PendingAction(
            action_id=action_id,
            user_id=user_id,
            tool_name=tool_name,
            tool_input=dict(tool_input),
            description=description,
            details=details,
            created_at=now,
        )

    async def claim(self, action_id: UUID, user_id: str) -> PendingAction:
        """Atomically retrieve-and-remove a pending action for execution.

        Raises ``ActionExpiredError`` if not found (expired or never existed)
        and ``ForbiddenError`` if the action belongs to a different user.
        """
        from sqlalchemy import delete, select

        from src.infrastructure.persistence.database.db_connection import get_session
        from src.infrastructure.persistence.database.db_models import DBPendingAction

        cutoff = datetime.now(UTC) - _TTL
        stmt = (
            delete(DBPendingAction)
            .where(
                DBPendingAction.id == action_id,
                DBPendingAction.user_id == user_id,
                DBPendingAction.created_at >= cutoff,
            )
            .returning(
                DBPendingAction.tool_name,
                DBPendingAction.tool_input,
                DBPendingAction.description,
                DBPendingAction.details,
                DBPendingAction.created_at,
            )
            .execution_options(synchronize_session=False)
        )
        async with get_session() as session:
            claimed = (await session.execute(stmt)).one_or_none()
            if claimed is not None:
                # Row typing doesn't flow through delete().returning(); the
                # tuple shape mirrors the .returning() column list above.
                tool_name, tool_input, description, details, created_at = cast(
                    "tuple[str, JsonDict, str, JsonDict, datetime]", tuple(claimed)
                )
                return PendingAction(
                    action_id=action_id,
                    user_id=user_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    description=description,
                    details=details,
                    created_at=created_at,
                )
            # Distinguish foreign from missing/expired for the error contract.
            owner = await session.scalar(
                select(DBPendingAction.user_id).where(DBPendingAction.id == action_id)
            )
        if owner is not None and owner != user_id:
            raise ForbiddenError("Cannot confirm another user's action")
        raise ActionExpiredError("This action has expired. Please try again.")

    async def cancel(self, action_id: UUID, user_id: str) -> None:
        """Remove a pending action without executing it (idempotent)."""
        from sqlalchemy import delete, select

        from src.infrastructure.persistence.database.db_connection import get_session
        from src.infrastructure.persistence.database.db_models import DBPendingAction

        stmt = (
            delete(DBPendingAction)
            .where(
                DBPendingAction.id == action_id,
                DBPendingAction.user_id == user_id,
            )
            .returning(DBPendingAction.id)
            .execution_options(synchronize_session=False)
        )
        async with get_session() as session:
            deleted = (await session.execute(stmt)).one_or_none()
            if deleted is not None:
                return
            owner = await session.scalar(
                select(DBPendingAction.user_id).where(DBPendingAction.id == action_id)
            )
        if owner is not None and owner != user_id:
            raise ForbiddenError("Cannot cancel another user's action")
        # Already expired or cancelled — idempotent.


pending_action_store: PendingActionStore = PostgresPendingActionStore()
