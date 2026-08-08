"""Context wiring done by ``execute_use_case`` around the use case body.

Asserts what a transaction opened inside the runner would apply, by firing the
production ``after_begin`` hook from within the use-case factory.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import Mock, patch

from src.application.runner import execute_use_case
from src.config.constants import BusinessLimits
from src.infrastructure.persistence.database.user_context import set_rls_user_on_begin


@asynccontextmanager
async def _fake_session(rollback: bool = True) -> AsyncGenerator[Mock]:
    _ = rollback
    yield Mock()


def _statements_a_transaction_would_run() -> list[str]:
    connection = Mock()
    transaction = Mock()
    # Assigned, not passed to the constructor: ``parent`` is a reserved Mock kwarg.
    transaction.parent = None
    set_rls_user_on_begin(Mock(), transaction, connection)
    return [str(call.args[0]) for call in connection.execute.call_args_list]


async def _run_capturing_statements(
    *,
    user_id: str | None = None,
    statement_timeout: str | None = None,
) -> list[str]:
    captured: list[str] = []

    async def factory(_uow: object) -> None:
        captured.extend(_statements_a_transaction_would_run())

    with (
        patch(
            "src.infrastructure.persistence.database.db_connection.get_session",
            _fake_session,
        ),
        patch(
            "src.infrastructure.persistence.repositories.factories.get_unit_of_work",
            Mock(return_value=Mock()),
        ),
    ):
        await execute_use_case(factory, user_id, statement_timeout=statement_timeout)
    return captured


class TestStatementTimeoutWiring:
    """``statement_timeout`` is opt-in and independent of ``user_id``."""

    async def test_no_timeout_context_is_entered_by_default(self) -> None:
        statements = await _run_capturing_statements(user_id="user-42")

        assert not any("statement_timeout" in sql for sql in statements)

    async def test_bulk_timeout_applies_to_transactions_inside_the_use_case(
        self,
    ) -> None:
        statements = await _run_capturing_statements(
            user_id="user-42",
            statement_timeout=BusinessLimits.BULK_IMPORT_STATEMENT_TIMEOUT,
        )

        assert any("statement_timeout" in sql for sql in statements)
        assert any("app.user_id" in sql for sql in statements)

    async def test_timeout_applies_without_a_user_id(self) -> None:
        statements = await _run_capturing_statements(statement_timeout="300s")

        assert any("statement_timeout" in sql for sql in statements)

    async def test_timeout_does_not_leak_past_the_runner(self) -> None:
        _ = await _run_capturing_statements(statement_timeout="300s")

        assert not any(
            "statement_timeout" in sql for sql in _statements_a_transaction_would_run()
        )
