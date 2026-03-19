"""Classify database exceptions into actionable user-facing messages.

Walks the exception __cause__ chain to identify the root cause (DNS failure,
auth error, timeout, etc.) and returns a structured result with a one-line
message the user can act on. Consumed by the API middleware, health endpoint,
and CLI error handler.
"""

import re
import socket
import ssl
from typing import Final, Literal

from attrs import frozen

type DatabaseErrorCategory = Literal[
    "dns_failure",
    "connection_refused",
    "ssl_error",
    "pool_exhaustion",
    "auth_failure",
    "timeout",
    "cold_start",
    "unknown",
]
from sqlalchemy.exc import TimeoutError as SATimeoutError

_AUTH_PGCODES: Final = frozenset({"28P01", "28000"})
_STATEMENT_TIMEOUT_PGCODE: Final = "57014"
_LOCK_TIMEOUT_PGCODE: Final = "55P03"

_HOST_RE: Final = re.compile(
    r'(?:connection to server at|host[=:])\s*["\']?([^\s"\',;:]+)',
)


@frozen
class DatabaseErrorInfo:
    """Classified database error with actionable guidance."""

    category: DatabaseErrorCategory
    user_message: str
    detail: str


def classify_database_error(exc: Exception) -> DatabaseErrorInfo:
    """Inspect exception chain and classify the database failure.

    Returns a ``DatabaseErrorInfo`` with:
    - ``category``: machine-readable error class for logs/frontend
    - ``user_message``: one-line actionable guidance
    - ``detail``: technical detail for structured logs
    """
    hostname = _extract_hostname(exc)
    host_hint = f" at {hostname}" if hostname else ""

    for cause in _iter_chain(exc):
        # DNS resolution failure
        if isinstance(cause, socket.gaierror):
            return DatabaseErrorInfo(
                category="dns_failure",
                user_message=f"Cannot resolve database hostname{host_hint} — check DATABASE_URL for typos",
                detail=str(cause),
            )

        # Connection refused (server not running / wrong port)
        if isinstance(cause, ConnectionRefusedError):
            return DatabaseErrorInfo(
                category="connection_refused",
                user_message=f"Cannot connect to database{host_hint} — is PostgreSQL running? Check DATABASE_URL",
                detail=str(cause),
            )

        # SSL/TLS errors
        if isinstance(cause, ssl.SSLError):
            return DatabaseErrorInfo(
                category="ssl_error",
                user_message=f"SSL connection to database{host_hint} failed — check sslmode in DATABASE_URL",
                detail=str(cause),
            )

        # SQLAlchemy pool timeout (all connections in use)
        if isinstance(cause, SATimeoutError):
            return DatabaseErrorInfo(
                category="pool_exhaustion",
                user_message="Database connection pool exhausted — too many concurrent queries",
                detail=str(cause),
            )

        # psycopg errors with pgcode
        pgcode = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        if pgcode:
            if pgcode in _AUTH_PGCODES:
                return DatabaseErrorInfo(
                    category="auth_failure",
                    user_message=f"Database authentication failed{host_hint} — check DATABASE_URL credentials",
                    detail=str(cause),
                )
            if pgcode == _STATEMENT_TIMEOUT_PGCODE:
                return DatabaseErrorInfo(
                    category="timeout",
                    user_message="Database query timed out — the operation took too long",
                    detail=str(cause),
                )
            if pgcode == _LOCK_TIMEOUT_PGCODE:
                return DatabaseErrorInfo(
                    category="timeout",
                    user_message="Database lock timeout — another operation is blocking this query",
                    detail=str(cause),
                )

        # Serverless database cold-start indicators (compute suspended)
        cause_str = str(cause).lower()
        if any(
            s in cause_str
            for s in ("endpoint is not active", "compute is waking", "compute is not running")
        ):
            return DatabaseErrorInfo(
                category="cold_start",
                user_message="Database server is waking up from idle suspend — retry in a few seconds",
                detail=str(cause),
            )

        # Statement timeout via error message (when pgcode not available)
        if "statement timeout" in cause_str:
            return DatabaseErrorInfo(
                category="timeout",
                user_message="Database query timed out — the operation took too long",
                detail=str(cause),
            )

    # Fallback — unknown database error
    return DatabaseErrorInfo(
        category="unknown",
        user_message=f"Database error occurred{host_hint}",
        detail=str(exc),
    )


def _iter_chain(exc: BaseException) -> list[BaseException]:
    """Collect all exceptions in the __cause__ / __context__ chain."""
    seen: set[int] = set()
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _extract_hostname(exc: Exception) -> str | None:
    """Try to extract the database hostname from the exception message."""
    for cause in _iter_chain(exc):
        match = _HOST_RE.search(str(cause))
        if match:
            return match.group(1)
    return None
