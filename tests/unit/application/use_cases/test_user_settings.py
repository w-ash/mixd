"""Unit tests for the user-settings use cases.

Real persistence (default merging, upsert) is covered against a database in
``tests/integration/api/test_settings.py``. What matters here is the wiring:
that reads stay out of a write transaction, and that a patch commits.
"""

from unittest.mock import AsyncMock, MagicMock

from src.application.use_cases.user_settings import (
    GetUserSettingsCommand,
    GetUserSettingsUseCase,
    PatchUserSettingsCommand,
    PatchUserSettingsUseCase,
)
from tests.fixtures import make_mock_uow


def _uow_with_settings(*, load=None, patch=None):
    uow = make_mock_uow()
    repo = MagicMock()
    repo.load = AsyncMock(return_value=load or {})
    repo.patch = AsyncMock(return_value=patch or {})
    uow.get_user_settings_repository = MagicMock(return_value=repo)
    return uow, repo


class TestGetUserSettings:
    async def test_returns_stored_settings(self) -> None:
        uow, repo = _uow_with_settings(load={"theme_mode": "light"})

        result = await GetUserSettingsUseCase().execute(
            GetUserSettingsCommand(user_id="u1"), uow
        )

        assert result.settings == {"theme_mode": "light"}
        repo.load.assert_awaited_once_with("u1")

    async def test_read_does_not_commit(self) -> None:
        """A GET holding open a write transaction would be a pointless lock."""
        uow, _ = _uow_with_settings(load={"theme_mode": "dark"})

        await GetUserSettingsUseCase().execute(
            GetUserSettingsCommand(user_id="u1"), uow
        )

        uow.commit.assert_not_awaited()


class TestPatchUserSettings:
    async def test_forwards_updates_and_commits(self) -> None:
        uow, repo = _uow_with_settings(patch={"theme_mode": "light"})

        result = await PatchUserSettingsUseCase().execute(
            PatchUserSettingsCommand(user_id="u1", updates={"theme_mode": "light"}), uow
        )

        assert result.settings == {"theme_mode": "light"}
        repo.patch.assert_awaited_once_with({"theme_mode": "light"}, "u1")
        uow.commit.assert_awaited_once()

    async def test_empty_update_still_round_trips(self) -> None:
        """An all-null PATCH body must return current settings, not an empty object."""
        uow, repo = _uow_with_settings(patch={"theme_mode": "dark"})

        result = await PatchUserSettingsUseCase().execute(
            PatchUserSettingsCommand(user_id="u1"), uow
        )

        assert result.settings == {"theme_mode": "dark"}
        repo.patch.assert_awaited_once_with({}, "u1")
