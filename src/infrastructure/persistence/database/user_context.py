"""Per-request user and statement-budget context for database transactions.

Provides ContextVars to propagate user identity and an opt-in statement
timeout through async call stacks, plus the single SQLAlchemy ``after_begin``
event handler that applies both to each PostgreSQL transaction — ``app.user_id``
for Row-Level Security enforcement, ``statement_timeout`` for bulk workloads.

Public API:
----------
user_context(user_id: str) -> ContextManager
    Set the current user for the duration of a block (async-safe via contextvars).
    Usage: with user_context("neon-auth-sub-123"): ...

get_current_user_id_from_context() -> str
    Read the current user ID from the contextvar.

statement_timeout_context(value: str) -> ContextManager
    Raise the per-transaction statement timeout for the duration of a block.
    Usage: with statement_timeout_context("300s"): ...
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

# ``None`` means "leave the connection default alone" — the 30s session-level
# statement_timeout set on connect in ``db_connection._set_connection_timeouts``.
_current_statement_timeout: ContextVar[str | None] = ContextVar(
    "_current_statement_timeout", default=None
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


@contextmanager
def statement_timeout_context(value: str) -> Generator[None]:
    """Raise the statement timeout for transactions opened inside a block.

    Opt-in budget for bulk work (import, rebuild); everything else keeps the
    30s connection default. The setting is applied transaction-locally by
    :func:`set_rls_user_on_begin`, so every COMMIT reverts to that default and
    the next autobegin re-applies the budget — which is what lets it survive a
    use case that commits per chunk rather than once at the end.

    Async-safe via PEP 567 contextvars — child coroutines inherit the value.
    Always resets in ``finally`` to prevent leakage on exceptions.

    Args:
        value: PostgreSQL interval literal, e.g. ``"300s"``.
    """
    token = _current_statement_timeout.set(value)
    try:
        yield
    finally:
        _current_statement_timeout.reset(token)


def set_rls_user_on_begin(
    _session: Session,
    transaction: SessionTransaction,
    connection: Connection,
) -> None:
    """SQLAlchemy ``after_begin`` event: per-transaction app.user_id and timeout.

    Called automatically when a new top-level transaction begins. Sets the
    PostgreSQL session variable that RLS policies reference via
    ``current_setting('app.user_id', TRUE)``, and — when
    :func:`statement_timeout_context` is active — the transaction's
    ``statement_timeout``.

    Implementation notes (2026 best practices):
    - Executes on the **connection**, not the session (SQLAlchemy 2.0.17+
      requirement — ``session.execute()`` inside ``after_begin`` raises
      "concurrent operations not permitted").
    - Uses ``set_config(..., true)`` for transaction-scoped setting — safe
      with Neon's PgBouncer in transaction mode (clears on COMMIT/ROLLBACK).
      ``SET LOCAL x = :v`` is not an alternative: SET takes no bind parameters.
    - **One statement, always** — transaction begin is a round trip, and bulk
      imports (which always set a timeout) open one per chunk. ``set_config``
      is a plain function, so both settings compose into one SELECT.
    - Skips savepoints (``transaction.parent is not None``) — only top-level
      transactions need the SET LOCAL; savepoints inherit both settings.
    - Single listener by design: registering a second ``after_begin`` hook
      re-runs on every session class the harness touches (see the
      double-registration note in ``live_rows.register_live_rows_filter``).
      ``create_session_factory`` enforces this with an ``event.contains`` guard.
    """
    if transaction.parent is not None:
        return  # Savepoint — inherit parent transaction's settings

    uid = _current_user_id.get()
    timeout = _current_statement_timeout.get()

    if timeout is None:
        connection.execute(
            text("SELECT set_config('app.user_id', :uid, true)"),
            {"uid": uid},
        )
    else:
        connection.execute(
            text(
                "SELECT set_config('app.user_id', :uid, true), "
                "set_config('statement_timeout', :timeout, true)"
            ),
            {"uid": uid, "timeout": timeout},
        )
