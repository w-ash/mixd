"""Unit tests for DeleteTagUseCase.

Thin pass-through over ``tag_repo.delete_tag``; verifies wiring + commit.
"""

from src.application.use_cases.delete_tag import (
    DeleteTagCommand,
    DeleteTagUseCase,
)
from tests.fixtures import make_mock_uow


class TestDeleteTag:
    async def test_forwards_to_repo_and_returns_count(self) -> None:
        uow = make_mock_uow()
        uow.get_tag_repository().delete_tag.return_value = 3

        result = await DeleteTagUseCase().execute(
            DeleteTagCommand(user_id="default", tag="TODO:check"),
            uow,
        )

        assert result.affected_count == 3
        uow.get_tag_repository().delete_tag.assert_awaited_once_with(
            user_id="default", tag="TODO:check"
        )
        uow.commit.assert_awaited()
