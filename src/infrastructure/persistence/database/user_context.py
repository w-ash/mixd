"""Per-request user context for multi-tenant data isolation.

Provides a ContextVar to propagate user identity through async call stacks
and a SQLAlchemy ``after_begin`` event handler that sets ``app.user_id`` on
each PostgreSQL transaction for Row-Level Security enforcement.

Public API:
----------
user_context(user_id: str) -> ContextManager
    Set the current user for the duration of a block (async-safe via contextvars).
    Usage: with user_context("neon-auth-sub-123"): ...

get_current_user_id_from_context() -> str
    Read the current user ID from the contextvar.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, SessionTransaction

from src.config.constants import BusinessLimits

_current_user_id: ContextVar[str] = ContextVar(
    "_current_user_id", default=BusinessLimits.DEFAULT_USER_ID
)


def get_current_user_id_from_context() -> str:
    """Read the current user ID from the contextvar."""
    return _current_user_id.get()


@contextmanager
def user_context(user_id: str) -> Generator[None]:
    """Set the current user for the duration of a block.

    Async-safe via PEP 567 contextvars — child coroutines inherit the value.
    Always resets in ``finally`` to prevent leakage on exceptions.

    Args:
        user_id: Neon Auth ``sub`` claim or ``DEFAULT_USER_ID`` for CLI.
    """
    token = _current_user_id.set(user_id)
    try:
        yield
    finally:
        _current_user_id.reset(token)


def set_rls_user_on_begin(
    _session: Session,
    transaction: SessionTransaction,
    connection: Connection,
) -> None:
    """SQLAlchemy ``after_begin`` event: SET LOCAL app.user_id per-transaction.

    Called automatically when a new top-level transaction begins. Sets the
    PostgreSQL session variable that RLS policies reference via
    ``current_setting('app.user_id', TRUE)``.

    Implementation notes (2026 best practices):
    - Executes on the **connection**, not the session (SQLAlchemy 2.0.17+
      requirement — ``session.execute()`` inside ``after_begin`` raises
      "concurrent operations not permitted").
    - Uses ``set_config(..., true)`` for transaction-scoped setting — safe
      with Neon's PgBouncer in transaction mode (clears on COMMIT/ROLLBACK).
    - Skips savepoints (``transaction.parent is not None``) — only top-level
      transactions need the SET LOCAL.
    """
    if transaction.parent is not None:
        return  # Savepoint — inherit parent transaction's setting

    uid = _current_user_id.get()
    connection.execute(
        text("SELECT set_config('app.user_id', :uid, true)"),
        {"uid": uid},
    )
