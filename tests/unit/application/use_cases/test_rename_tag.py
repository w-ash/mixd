"""Unit tests for RenameTagUseCase.

The use case is a thin pass-through: it forwards to ``tag_repo.rename_tag``
and wraps the affected count in a Result. Tests verify the wiring + that
the UoW is committed.
"""

from src.application.use_cases.rename_tag import (
    RenameTagCommand,
    RenameTagUseCase,
)
from tests.fixtures import make_mock_uow


class TestRenameTag:
    async def test_forwards_to_repo_and_returns_count(self) -> None:
        uow = make_mock_uow()
        uow.get_tag_repository().rename_tag.return_value = 7

        result = await RenameTagUseCase().execute(
            RenameTagCommand(
                user_id="default", source="mood:chill", target="mood:ambient"
            ),
            uow,
        )

        assert result.affected_count == 7
        uow.get_tag_repository().rename_tag.assert_awaited_once_with(
            user_id="default", source="mood:chill", target="mood:ambient"
        )
        uow.commit.assert_awaited()
