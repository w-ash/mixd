"""Rename a tag across all of one user's tracks.

Bulk operation: a single rename call updates every track that currently
carries ``source`` to carry ``target`` instead. Idempotent on tracks
that already have ``target`` (no duplicate row created). Source rows
are removed; per-track event rows record the change.

Used by the Tag Management page (``PATCH /api/v1/tags/{tag}``) and is
the primitive that ``MergeTagsUseCase`` also wraps.
"""

from attrs import define

from src.application.runner import execute_use_case
from src.config import get_logger
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class RenameTagCommand:
    user_id: str
    source: str
    target: str


@define(frozen=True, slots=True)
class RenameTagResult:
    affected_count: int


@define(slots=True)
class RenameTagUseCase:
    async def execute(
        self,
        command: RenameTagCommand,
        uow: UnitOfWorkProtocol,
    ) -> RenameTagResult:
        async with uow:
            tag_repo = uow.get_tag_repository()
            affected = await tag_repo.rename_tag(
                user_id=command.user_id,
                source=command.source,
                target=command.target,
            )
            await uow.commit()
            return RenameTagResult(affected_count=affected)


async def run_rename_tag(user_id: str, source: str, target: str) -> RenameTagResult:
    """Rename a tag via execute_use_case."""
    command = RenameTagCommand(user_id=user_id, source=source, target=target)
    return await execute_use_case(
        lambda uow: RenameTagUseCase().execute(command, uow),
        user_id=user_id,
    )
