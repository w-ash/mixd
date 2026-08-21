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


class DefaultUserOnRemoteDatabaseError(RuntimeError):
    """A transaction tried to open as ``default`` against a hosted database."""


_system_operation: ContextVar[bool] = ContextVar("_system_operation", default=False)


@contextmanager
def system_context() -> Generator[None]:
    """Mark a transaction as cross-tenant maintenance with no owning user.

    A handful of operations legitimately run without a user: pruning expired
    OAuth CSRF state, reaping dead operation runs, sweeping stalled workflow
    runs. They are global by design — ``prune_expired_states`` is a ``DELETE``
    with no user predicate at all.

    Until now those were indistinguishable from a bug, because both showed up
    as ``DEFAULT_USER_ID`` on the contextvar. This makes "no tenant, on
    purpose" a declared thing, so :func:`set_rls_user_on_begin` can refuse the
    accidental case without also breaking the deliberate one. ``app.user_id``
    is unchanged — only the guard's decision differs.
    """
    token = _system_operation.set(True)
    try:
        yield
    finally:
        _system_operation.reset(token)


def _refuse_default_user_on_remote(uid: str) -> None:
    """Refuse to open a transaction as ``default`` against a remote database.

    ``DEFAULT_USER_ID`` is local-dev scaffolding (``config/constants.py``): it
    is never a real account, so rows written under it on a hosted database are
    always an accident. Production carries three such tenants, from three
    different doors — a pre-multi-user snapshot, a CLI invocation that picked
    up ``.env.local``'s production URL, and a reproduction script. The CLI, the
    API (``deps.get_current_user_id`` returns this sentinel whenever auth is
    unconfigured, which is what ``pnpm dev`` does) and ad-hoc scripts are three
    entry points, and this event is the one place all three converge.

    Fails closed: a URL that cannot be parsed counts as remote. Set
    ``MIXD_USER_ID`` to the account you mean, or point ``DATABASE_URL`` at
    localhost.
    """
    from src.config.settings import database_host_and_mode, get_database_url

    host, mode = database_host_and_mode(get_database_url())
    if mode == "local":
        return
    raise DefaultUserOnRemoteDatabaseError(
        f"Refusing to open a transaction as {uid!r} against remote database "
        f"{host!r}. {uid!r} is local-dev only — writing it to a hosted "
        f"database creates a tenant nobody owns. Set MIXD_USER_ID to the "
        f"account you mean, or point DATABASE_URL at localhost. "
        f"(Cross-tenant maintenance should declare system_context().)"
    )


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
    if uid == BusinessLimits.DEFAULT_USER_ID and not _system_operation.get():
        _refuse_default_user_on_remote(uid)
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
