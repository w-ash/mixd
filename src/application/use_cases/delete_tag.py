"""Bulk-delete a tag from all of one user's tracks.

Cascades to the tag-event log per the v0.7.6 product decision: when a
tag is deleted, the audit trail's subject no longer exists, so its
event rows are deleted too. No remove events are written.
"""

from attrs import define

from src.application.runner import execute_use_case
from src.config import get_logger
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class DeleteTagCommand:
    user_id: str
    tag: str


@define(frozen=True, slots=True)
class DeleteTagResult:
    affected_count: int


@define(slots=True)
class DeleteTagUseCase:
    async def execute(
        self,
        command: DeleteTagCommand,
        uow: UnitOfWorkProtocol,
    ) -> DeleteTagResult:
        async with uow:
            tag_repo = uow.get_tag_repository()
            affected = await tag_repo.delete_tag(
                user_id=command.user_id, tag=command.tag
            )
            await uow.commit()
            return DeleteTagResult(affected_count=affected)


async def run_delete_tag(user_id: str, tag: str) -> DeleteTagResult:
    """Delete a tag via execute_use_case."""
    command = DeleteTagCommand(user_id=user_id, tag=tag)
    return await execute_use_case(
        lambda uow: DeleteTagUseCase().execute(command, uow),
        user_id=user_id,
    )
