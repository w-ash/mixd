"""Reading a PostgreSQL SQLSTATE off whatever exception actually arrives.

The caller never catches the driver error. It catches a SQLAlchemy wrapper
holding the psycopg error in ``.orig``, sometimes chained behind another
exception — so the lookup has to walk, and it has to do so without importing
either library (this is the domain kernel, and the application layer that
calls it may not import them at all).
"""

from src.domain.repositories.errors import (
    DEADLOCK_DETECTED,
    LOCK_NOT_AVAILABLE,
    SERIALIZATION_FAILURE,
    is_transient_contention,
    postgres_sqlstate,
)

UNIQUE_VIOLATION = "23505"


class _PsycopgError(Exception):
    """Shape of a psycopg3 error: the code lives on ``sqlstate``."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"pg error {sqlstate}")
        self.sqlstate = sqlstate


class _Psycopg2Error(Exception):
    """Shape of a psycopg2 error: the same value spelled ``pgcode``."""

    def __init__(self, pgcode: str) -> None:
        super().__init__(f"pg error {pgcode}")
        self.pgcode = pgcode


class _SqlalchemyWrapper(Exception):
    """Shape of ``sqlalchemy.exc.DBAPIError``: driver error hangs off ``orig``."""

    def __init__(self, orig: Exception) -> None:
        super().__init__("(psycopg.errors...) wrapped")
        self.orig = orig


class TestReadingTheSqlstate:
    def test_it_reads_through_the_sqlalchemy_wrapper(self):
        wrapped = _SqlalchemyWrapper(_PsycopgError(LOCK_NOT_AVAILABLE))

        assert postgres_sqlstate(wrapped) == LOCK_NOT_AVAILABLE

    def test_it_accepts_the_psycopg2_spelling(self):
        assert postgres_sqlstate(_Psycopg2Error(DEADLOCK_DETECTED)) == DEADLOCK_DETECTED

    def test_it_follows_the_cause_chain(self):
        """A repository that re-raises its own error still carries the code.

        ``__cause__`` set directly is exactly what ``raise ... from`` does.
        """
        reraised = RuntimeError("ingest failed")
        reraised.__cause__ = _SqlalchemyWrapper(_PsycopgError(SERIALIZATION_FAILURE))

        assert postgres_sqlstate(reraised) == SERIALIZATION_FAILURE

    def test_a_plain_python_error_has_no_sqlstate(self):
        assert postgres_sqlstate(ValueError("not a database error")) is None


class TestClassifyingContention:
    def test_the_three_contention_codes_are_transient(self):
        for code in (LOCK_NOT_AVAILABLE, DEADLOCK_DETECTED, SERIALIZATION_FAILURE):
            assert is_transient_contention(_SqlalchemyWrapper(_PsycopgError(code)))

    def test_a_constraint_violation_is_not(self):
        """The distinction the ingest fallback turns on: this one is about the
        row, so it earns the per-track retry, not a whole-batch replay."""
        violation = _SqlalchemyWrapper(_PsycopgError(UNIQUE_VIOLATION))

        assert not is_transient_contention(violation)

    def test_a_non_database_error_is_not(self):
        assert not is_transient_contention(RuntimeError("connector returned junk"))
