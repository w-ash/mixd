"""Unit tests for ListTagsUseCase."""

from src.application.use_cases.list_tags import ListTagsCommand, ListTagsUseCase
from tests.fixtures import make_mock_uow


class TestListTags:
    async def test_passes_query_and_limit_through(self) -> None:
        uow = make_mock_uow()
        uow.get_tag_repository().list_tags.return_value = [("mood:chill", 5)]

        result = await ListTagsUseCase().execute(
            ListTagsCommand(user_id="default", query="mood", limit=20), uow
        )

        assert result.tags == [("mood:chill", 5)]
        uow.get_tag_repository().list_tags.assert_called_once_with(
            user_id="default", query="mood", limit=20
        )

    async def test_defaults_query_to_none(self) -> None:
        uow = make_mock_uow()
        uow.get_tag_repository().list_tags.return_value = []

        await ListTagsUseCase().execute(ListTagsCommand(user_id="default"), uow)

        uow.get_tag_repository().list_tags.assert_called_once_with(
            user_id="default", query=None, limit=100
        )
