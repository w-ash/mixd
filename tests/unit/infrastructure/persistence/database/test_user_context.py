"""Per-transaction context injection for RLS and the bulk statement budget.

Covers the single ``after_begin`` hook: it always sets ``app.user_id``, sets
``statement_timeout`` only while :func:`statement_timeout_context` is active,
and does neither on savepoints.
"""

from unittest.mock import Mock

import pytest

from src.infrastructure.persistence.database.user_context import (
    get_current_user_id_from_context,
    set_rls_user_on_begin,
    statement_timeout_context,
    user_context,
)


def _executed_sql(connection: Mock) -> list[str]:
    return [str(call.args[0]) for call in connection.execute.call_args_list]


def _bound_params(connection: Mock) -> list[dict[str, str]]:
    return [call.args[1] for call in connection.execute.call_args_list]


def _fire_hook(*, parent: object | None = None) -> Mock:
    """Run the hook against a mock connection and return that connection."""
    connection = Mock()
    transaction = Mock()
    # Assigned, not passed to the constructor: ``parent`` is a reserved Mock kwarg.
    transaction.parent = parent
    set_rls_user_on_begin(Mock(), transaction, connection)
    return connection


class TestStatementTimeoutOnBegin:
    """The timeout is opt-in: no context, no statement_timeout statement."""

    def test_timeout_is_not_set_when_no_context_is_active(self) -> None:
        connection = _fire_hook()

        sql = _executed_sql(connection)
        assert len(sql) == 1
        assert "app.user_id" in sql[0]
        assert "statement_timeout" not in sql[0]

    def test_timeout_is_set_transaction_locally_inside_the_context(self) -> None:
        with statement_timeout_context("300s"):
            connection = _fire_hook()

        sql = _executed_sql(connection)
        # One statement, not two: transaction begin is a round trip, and bulk
        # imports open one per chunk.
        assert len(sql) == 1
        # ``true`` is the is_local flag — reverts on COMMIT, unlike a bare SET.
        assert "set_config('statement_timeout', :timeout, true)" in sql[0]
        assert _bound_params(connection)[0]["timeout"] == "300s"

    def test_user_id_is_still_set_alongside_the_timeout(self) -> None:
        with user_context("user-42"), statement_timeout_context("300s"):
            connection = _fire_hook()

        assert _bound_params(connection)[0]["uid"] == "user-42"

    def test_savepoints_set_neither_setting(self) -> None:
        with statement_timeout_context("300s"):
            connection = _fire_hook(parent=Mock())

        assert connection.execute.call_args_list == []


class TestStatementTimeoutContextLifetime:
    """The contextvar must not leak past the block that set it."""

    def test_timeout_reverts_after_the_block_exits(self) -> None:
        with statement_timeout_context("300s"):
            pass

        connection = _fire_hook()

        assert len(_executed_sql(connection)) == 1

    def test_timeout_reverts_when_the_block_raises(self) -> None:
        def _fail() -> None:
            raise RuntimeError("import blew up")

        with pytest.raises(RuntimeError), statement_timeout_context("300s"):
            _fail()

        connection = _fire_hook()

        assert len(_executed_sql(connection)) == 1

    def test_nested_contexts_restore_the_outer_budget(self) -> None:
        with statement_timeout_context("300s"):
            with statement_timeout_context("60s"):
                inner = _fire_hook()
            outer = _fire_hook()

        assert _bound_params(inner)[0]["timeout"] == "60s"
        assert _bound_params(outer)[0]["timeout"] == "300s"

    def test_user_context_is_unaffected_by_the_timeout_context(self) -> None:
        with user_context("user-42"), statement_timeout_context("300s"):
            assert get_current_user_id_from_context() == "user-42"
