"""Unit tests for MergeTagsUseCase.

Thin pass-through over ``tag_repo.merge_tags`` (which itself aliases
``rename_tag``); verifies wiring + commit.
"""

from src.application.use_cases.merge_tags import (
    MergeTagsCommand,
    MergeTagsUseCase,
)
from tests.fixtures import make_mock_uow


class TestMergeTags:
    async def test_forwards_to_repo_and_returns_count(self) -> None:
        uow = make_mock_uow()
        uow.get_tag_repository().merge_tags.return_value = 5

        result = await MergeTagsUseCase().execute(
            MergeTagsCommand(
                user_id="default",
                source="context:gym",
                target="context:workout",
            ),
            uow,
        )

        assert result.affected_count == 5
        uow.get_tag_repository().merge_tags.assert_awaited_once_with(
            user_id="default",
            source="context:gym",
            target="context:workout",
        )
        uow.commit.assert_awaited()
