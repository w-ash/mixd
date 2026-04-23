"""Merge two tags: ``source`` becomes ``target`` across all of one user's tracks.

Same primitive as ``RenameTagUseCase`` (the repo collapses both to
``rename_tag``), surfaced as a separate use case so the API can expose
distinct rename / merge endpoints when the UX intent differs (rename =
fix a typo, merge = consolidate two existing tag families).
"""

from attrs import define

from src.application.runner import execute_use_case
from src.config import get_logger
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class MergeTagsCommand:
    user_id: str
    source: str
    target: str


@define(frozen=True, slots=True)
class MergeTagsResult:
    affected_count: int


@define(slots=True)
class MergeTagsUseCase:
    async def execute(
        self,
        command: MergeTagsCommand,
        uow: UnitOfWorkProtocol,
    ) -> MergeTagsResult:
        async with uow:
            tag_repo = uow.get_tag_repository()
            affected = await tag_repo.merge_tags(
                user_id=command.user_id,
                source=command.source,
                target=command.target,
            )
            await uow.commit()
            return MergeTagsResult(affected_count=affected)


async def run_merge_tags(user_id: str, source: str, target: str) -> MergeTagsResult:
    """Merge tags via execute_use_case."""
    command = MergeTagsCommand(user_id=user_id, source=source, target=target)
    return await execute_use_case(
        lambda uow: MergeTagsUseCase().execute(command, uow),
        user_id=user_id,
    )
