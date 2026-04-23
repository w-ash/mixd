"""List a user's tags with usage counts and last-used timestamps.

Powers both the autocomplete input (query-filtered) and the full tag
browser / management page (query=None). Trigram-aware — when ``query``
is set, matches use the GIN index on ``tag`` for sub-millisecond lookups.
"""

from datetime import datetime

from attrs import define

from src.config import get_logger
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ListTagsCommand:
    user_id: str
    query: str | None = None
    limit: int = 100


@define(frozen=True, slots=True)
class ListTagsResult:
    # (tag, track_count, last_used_at) ordered by count desc
    tags: list[tuple[str, int, datetime]]


@define(slots=True)
class ListTagsUseCase:
    async def execute(
        self,
        command: ListTagsCommand,
        uow: UnitOfWorkProtocol,
    ) -> ListTagsResult:
        async with uow:
            tag_repo = uow.get_tag_repository()
            tags = await tag_repo.list_tags(
                user_id=command.user_id,
                query=command.query,
                limit=command.limit,
            )
            return ListTagsResult(tags=tags)


async def run_list_tags(
    user_id: str,
    query: str | None = None,
    limit: int = 100,
) -> ListTagsResult:
    """List tags with counts via execute_use_case."""
    from src.application.runner import execute_use_case

    command = ListTagsCommand(user_id=user_id, query=query, limit=limit)
    return await execute_use_case(
        lambda uow: ListTagsUseCase().execute(command, uow),
        user_id=user_id,
    )
