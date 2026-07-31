"""Unit tests for the database reset use case.

The truncation itself is exercised against real SQL in
``tests/integration/test_admin_reset.py``. What matters here is the wiring:
that the preserved set actually reaches the repository, and that the result
carries what the CLI reports back to the user.
"""

from unittest.mock import AsyncMock, MagicMock

from src.application.use_cases.reset_database import (
    PRESERVED_TABLES,
    ResetDatabaseCommand,
    ResetDatabaseUseCase,
)
from tests.fixtures import make_mock_uow


def _uow_with_admin(truncated: list[str]):
    uow = make_mock_uow()
    admin = MagicMock()
    admin.truncate_data_tables = AsyncMock(return_value=truncated)
    uow.get_admin_repository = MagicMock(return_value=admin)
    return uow, admin


class TestResetDatabase:
    async def test_passes_the_preserved_set_to_the_repository(self) -> None:
        """Credentials surviving a reset is the contract; the repo can't guess it."""
        uow, admin = _uow_with_admin(["tracks", "plays"])

        await ResetDatabaseUseCase().execute(ResetDatabaseCommand(), uow)

        admin.truncate_data_tables.assert_awaited_once_with(PRESERVED_TABLES)

    async def test_reports_truncated_tables_and_commits(self) -> None:
        uow, _ = _uow_with_admin(["tracks", "plays"])

        result = await ResetDatabaseUseCase().execute(ResetDatabaseCommand(), uow)

        assert result.truncated_tables == ("tracks", "plays")
        uow.commit.assert_awaited_once()

    async def test_empty_schema_still_returns_a_result(self) -> None:
        """A no-op reset must not look like a failure to the caller."""
        uow, _ = _uow_with_admin([])

        result = await ResetDatabaseUseCase().execute(ResetDatabaseCommand(), uow)

        assert result.truncated_tables == ()


class TestPreservedTables:
    def test_covers_the_credentials_a_user_cannot_redo(self) -> None:
        assert {"oauth_tokens", "oauth_states"} <= PRESERVED_TABLES
