"""Assistant substrate: chat feedback and pending two-phase actions."""

from sqlalchemy import (
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    PgJsonb,
)


class DBChatFeedback(BaseEntity):
    """A human thumbs-up/thumbs-down on an assistant-generated workflow definition.

    Write-once: rows are inserted and never updated (no update path in the
    repository). No RLS policy — like ``schedules`` — per-user isolation is
    enforced by the repository's ``WHERE user_id`` filter rather than a
    database policy (migration 036). The CHECK constraint on ``signal`` lives
    in migration 036 only, per the codebase convention (CHECKs never in
    ``__table_args__``).
    """

    __tablename__: str = "chat_feedback"
    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_chat_feedback_user_id", "user_id"),
    )

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    prompt: Mapped[str] = mapped_column(Text(), nullable=False)
    generated_workflow_def: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    signal: Mapped[str] = mapped_column(String(), nullable=False)
    note: Mapped[str | None] = mapped_column(Text(), nullable=True)


class DBPendingAction(BaseEntity):
    """A proposed mutation awaiting user confirmation (two-phase writes).

    Durable so a proposal made on one machine is confirmable on another
    (Fly ``auto_start_machines`` + the v0.9.5 remote MCP transport). Rows are
    short-lived: 5-minute TTL, evicted opportunistically on every create.

    No RLS policy — like ``chat_feedback`` (migration 036): the store must
    see a foreign row's owner to distinguish ``ForbiddenError`` from
    ``ActionExpiredError``, so isolation is enforced by explicit ``user_id``
    predicates in every store query rather than a database policy
    (migration 038).
    """

    __tablename__: str = "pending_actions"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(), nullable=False)
    tool_input: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    details: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
