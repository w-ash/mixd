"""Failure taxonomy for the persistence contract.

The protocols in this package say what a write means; this module says what a
*failed* write means, in the one dimension a caller has to act on: is the
failure about the rows (a bad value, a violated constraint — retrying changes
nothing) or about the clock (another transaction is holding the same key right
now — retrying is the whole fix)?

Reading a PostgreSQL SQLSTATE needs no driver import. SQLAlchemy's wrapper
exposes the DBAPI exception as ``.orig``, and every psycopg error carries
``.sqlstate`` (psycopg2 spells the same value ``.pgcode``), so the lookup is
structural: stdlib only, no I/O, pure. That is what lets the *application*
layer branch on a SQLSTATE without importing SQLAlchemy or psycopg, which it
may not do.

Deliberately not shared with ``src/interface/_shared/error_classification.py``,
which reads some of the same codes: that module lives outward of here (it can
import this, never the reverse) and answers a different question — what do we
tell the user? — where this one answers whether the caller should retry.
"""

from collections.abc import Iterator
from typing import Final

# PostgreSQL SQLSTATEs that mean "someone else got here first", not "this data
# is wrong". None of the three is a property of the rows being written, so the
# correct response to all three is to retry the *identical* statement — never
# to pick the batch apart looking for a bad row.
LOCK_NOT_AVAILABLE: Final = "55P03"  # lock_timeout fired waiting on a row lock
DEADLOCK_DETECTED: Final = "40P01"  # the deadlock detector picked us as victim
SERIALIZATION_FAILURE: Final = "40001"  # concurrent update, REPEATABLE READ+

TRANSIENT_CONTENTION_SQLSTATES: Final = frozenset({
    LOCK_NOT_AVAILABLE,
    DEADLOCK_DETECTED,
    SERIALIZATION_FAILURE,
})


def postgres_sqlstate(exc: BaseException) -> str | None:
    """The PostgreSQL SQLSTATE behind ``exc``, or None if it isn't a DB error.

    Walks the ``__cause__``/``__context__`` chain and each link's ``.orig``,
    because the exception a repository caller actually catches is a SQLAlchemy
    wrapper several hops away from the driver error that carries the code.
    """
    for candidate in _error_chain(exc):
        code = _sqlstate_attribute(candidate)
        if code is not None:
            return code
    return None


def is_transient_contention(exc: BaseException) -> bool:
    """True when ``exc`` is lock contention, deadlock, or a serialization loss.

    The distinction that matters to a continue-on-error caller: a False here
    means the batch may contain a row worth isolating, while a True means every
    row in it is fine and the only variable is time.
    """
    return postgres_sqlstate(exc) in TRANSIENT_CONTENTION_SQLSTATES


def _error_chain(exc: BaseException) -> Iterator[object]:
    """Yield each exception in the cause/context chain plus its ``.orig``."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        orig = _driver_error_attribute(current)
        if orig is not None:
            yield orig
        current = current.__cause__ or current.__context__


def _driver_error_attribute(exc: BaseException) -> object:
    """The wrapped DBAPI exception (SQLAlchemy's ``.orig``), if there is one."""
    return getattr(exc, "orig", None)


def _sqlstate_attribute(candidate: object) -> str | None:
    """The SQLSTATE a psycopg error carries, under either spelling."""
    code: object = getattr(candidate, "sqlstate", None)
    if not isinstance(code, str):
        code = getattr(candidate, "pgcode", None)
    return code if isinstance(code, str) else None
