"""Repository for pending-action rows (two-phase confirmed writes).

Multi-machine-safe: ``claim_owned`` is a single conditional
``DELETE … RETURNING``, so exactly one machine can win a confirmation even
when two race on the same token.

NO RLS on this table (``chat_feedback`` / ``schedules`` precedent, migration
038): the caller must be able to see a foreign row's owner to distinguish
"someone else's action" from "expired", and RLS invisibility would collapse
the two. Isolation is the explicit ``user_id`` predicate in every query here.
"""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.pending_action import PendingAction
from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.db_models import DBPendingAction
from src.infrastructure.persistence.repositories.repo_decorator import db_operation


class PendingActionRepository:
    """Persistence for ``pending_actions`` rows — insert, claim, cancel."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @db_operation("insert_pending_action")
    async def insert_evicting_expired(
        self,
        *,
        user_id: str,
        tool_name: str,
        tool_input: JsonDict,
        description: str,
        details: JsonDict,
        created_at: datetime,
        cutoff: datetime,
    ) -> PendingAction:
        """Insert one action, first deleting any row older than ``cutoff``."""
        row = DBPendingAction(
            user_id=user_id,
            tool_name=tool_name,
            tool_input=dict(tool_input),
            description=description,
            details=details,
            created_at=created_at,
        )
        # Opportunistic eviction — these rows are short-lived and no
        # background sweeper covers them.
        await self._session.execute(
            delete(DBPendingAction).where(DBPendingAction.created_at < cutoff)
        )
        self._session.add(row)
        await self._session.flush()
        return PendingAction(
            action_id=row.id,
            user_id=user_id,
            tool_name=tool_name,
            tool_input=dict(tool_input),
            description=description,
            details=details,
            created_at=created_at,
        )

    @db_operation("claim_pending_action")
    async def claim_owned(
        self, action_id: UUID, user_id: str, cutoff: datetime
    ) -> PendingAction | None:
        """Atomically retrieve-and-remove an unexpired action owned by ``user_id``."""
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
        claimed = (await self._session.execute(stmt)).one_or_none()
        if claimed is None:
            return None
        # Row typing doesn't flow through delete().returning(); the tuple
        # shape mirrors the .returning() column list above.
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

    @db_operation("cancel_pending_action")
    async def delete_owned(self, action_id: UUID, user_id: str) -> bool:
        """Remove an action owned by ``user_id``; ``True`` when a row went."""
        stmt = (
            delete(DBPendingAction)
            .where(
                DBPendingAction.id == action_id,
                DBPendingAction.user_id == user_id,
            )
            .returning(DBPendingAction.id)
            .execution_options(synchronize_session=False)
        )
        return (await self._session.execute(stmt)).one_or_none() is not None

    @db_operation("get_pending_action_owner")
    async def get_owner(self, action_id: UUID) -> str | None:
        """Owner of ``action_id`` regardless of expiry, or ``None`` if absent."""
        return await self._session.scalar(
            select(DBPendingAction.user_id).where(DBPendingAction.id == action_id)
        )


__all__ = ["PendingActionRepository"]
