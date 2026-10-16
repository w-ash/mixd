"""Tests for database error classification."""

import socket
import ssl

from psycopg import OperationalError as PsycopgOperationalError
import pytest
from sqlalchemy.exc import (
    OperationalError as SAOperationalError,
    TimeoutError as SATimeoutError,
)

from src.interface._shared.error_classification import classify_database_error


def _make_sa_error(cause: BaseException) -> SAOperationalError:
    """Wrap a cause in an SQLAlchemy OperationalError chain."""
    sa_exc = SAOperationalError("test", {}, cause)
    sa_exc.__cause__ = cause
    return sa_exc


class TestClassifyDatabaseError:
    """Test the classify_database_error function."""

    def test_connection_refused(self) -> None:
        cause = ConnectionRefusedError("Connection refused")
        exc = _make_sa_error(cause)

        info = classify_database_error(exc)

        assert info.category == "connection_refused"
        assert "Cannot connect to database" in info.user_message
        assert "Check DATABASE_URL" in info.user_message

    def test_dns_failure(self) -> None:
        cause = socket.gaierror(8, "Name or service not known")
        exc = _make_sa_error(cause)

        info = classify_database_error(exc)

        assert info.category == "dns_failure"
        assert "Cannot resolve database hostname" in info.user_message
        assert "DATABASE_URL" in info.user_message

    def test_ssl_error(self) -> None:
        cause = ssl.SSLError(1, "[SSL: CERTIFICATE_VERIFY_FAILED]")
        exc = _make_sa_error(cause)

        info = classify_database_error(exc)

        assert info.category == "ssl_error"
        assert "SSL" in info.user_message

    def test_pool_exhaustion(self) -> None:
        exc = SATimeoutError("QueuePool limit reached")

        info = classify_database_error(exc)

        assert info.category == "pool_exhaustion"
        assert "pool exhausted" in info.user_message

    @pytest.mark.parametrize(
        ("sqlstate", "message"),
        [
            (
                "28P01",
                'connection failed: FATAL:  password authentication failed for user "bad_user"',
            ),
            ("28000", "connection failed: FATAL:  no pg_hba.conf entry"),
        ],
        ids=["invalid-password", "invalid-authorization"],
    )
    def test_auth_failure_pgcodes(self, sqlstate: str, message: str) -> None:
        psycopg_exc = PsycopgOperationalError(message)
        psycopg_exc.sqlstate = sqlstate
        exc = _make_sa_error(psycopg_exc)

        info = classify_database_error(exc)

        assert info.category == "auth_failure"
        assert "authentication failed" in info.user_message
        assert "credentials" in info.user_message

    def test_statement_timeout_pgcode(self) -> None:
        psycopg_exc = PsycopgOperationalError(
            "canceling statement due to statement timeout"
        )
        psycopg_exc.sqlstate = "57014"
        exc = _make_sa_error(psycopg_exc)

        info = classify_database_error(exc)

        assert info.category == "timeout"
        assert "timed out" in info.user_message

    def test_lock_timeout_pgcode(self) -> None:
        psycopg_exc = PsycopgOperationalError("canceling statement due to lock timeout")
        psycopg_exc.sqlstate = "55P03"
        exc = _make_sa_error(psycopg_exc)

        info = classify_database_error(exc)

        assert info.category == "timeout"
        assert "lock timeout" in info.user_message

    def test_statement_timeout_text_fallback(self) -> None:
        """When pgcode is unavailable, match on error message text."""
        cause = Exception("ERROR: canceling statement due to statement timeout")
        exc = _make_sa_error(cause)

        info = classify_database_error(exc)

        assert info.category == "timeout"

    def test_serverless_cold_start(self) -> None:
        psycopg_exc = PsycopgOperationalError(
            "connection to server at 'ep-xxx.us-west-2.aws.neon.tech' failed: "
            "the endpoint is not active, timeout while waiting"
        )
        exc = _make_sa_error(psycopg_exc)

        info = classify_database_error(exc)

        assert info.category == "cold_start"
        assert "waking up" in info.user_message
        assert "retry" in info.user_message

    def test_unknown_error(self) -> None:
        exc = SAOperationalError("something unexpected", {}, Exception("???"))
        exc.__cause__ = Exception("???")

        info = classify_database_error(exc)

        assert info.category == "unknown"
        assert "Database error occurred" in info.user_message

    def test_hostname_extraction_from_message(self) -> None:
        psycopg_exc = PsycopgOperationalError(
            'connection to server at "ep-super-glade.neon.tech", port 5432 failed'
        )
        exc = _make_sa_error(ConnectionRefusedError("refused"))
        # Inject the psycopg error earlier in the chain so hostname is found
        exc.__cause__ = psycopg_exc
        psycopg_exc.__cause__ = ConnectionRefusedError("refused")

        info = classify_database_error(exc)

        assert info.category == "connection_refused"
        assert "ep-super-glade.neon.tech" in info.user_message
