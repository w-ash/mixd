"""Tag-vocabulary maintenance: delete, rename, and merge across one user's tracks.

Bundles the three bulk vocabulary operations (mirroring the sanctioned
bundling in ``workflow_crud.py``/``schedules.py``): identical envelope, one
repository primitive each. Each operation keeps its own Command/Result
surface so the API can expose distinct endpoints where the UX intent
differs (rename = fix a typo, merge = consolidate two tag families).

Product notes preserved from the standalone modules:

- **Delete** cascades to the tag-event log per the v0.7.6 decision: when a
  tag is deleted, the audit trail's subject no longer exists, so its event
  rows are deleted too. No remove events are written.
- **Rename** is idempotent on tracks that already carry the target (no
  duplicate row); source rows are removed; per-track event rows record the
  change. It is the primitive that merge also wraps.
- **Merge** is the same repo primitive surfaced separately (the repo
  collapses both to ``rename_tag``).
"""

from collections.abc import Awaitable, Callable

from attrs import define

from src.domain.repositories.tag import TagRepositoryProtocol
from src.domain.repositories.uow import UnitOfWorkProtocol


async def _bulk_tag_mutation(
    uow: UnitOfWorkProtocol,
    mutate: Callable[[TagRepositoryProtocol], Awaitable[int]],
) -> int:
    """Run one bulk vocabulary mutation inside its transaction envelope."""
    async with uow:
        affected = await mutate(uow.get_tag_repository())
        await uow.commit()
        return affected


@define(frozen=True, slots=True)
class DeleteTagCommand:
    user_id: str
    tag: str


@define(frozen=True, slots=True)
class DeleteTagResult:
    affected_count: int


@define(slots=True)
class DeleteTagUseCase:
    """Bulk-delete a tag from all of one user's tracks."""

    async def execute(
        self,
        command: DeleteTagCommand,
        uow: UnitOfWorkProtocol,
    ) -> DeleteTagResult:
        affected = await _bulk_tag_mutation(
            uow,
            lambda repo: repo.delete_tag(user_id=command.user_id, tag=command.tag),
        )
        return DeleteTagResult(affected_count=affected)


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
    """Rename a tag across all of one user's tracks (``PATCH /api/v1/tags/{tag}``)."""

    async def execute(
        self,
        command: RenameTagCommand,
        uow: UnitOfWorkProtocol,
    ) -> RenameTagResult:
        affected = await _bulk_tag_mutation(
            uow,
            lambda repo: repo.rename_tag(
                user_id=command.user_id,
                source=command.source,
                target=command.target,
            ),
        )
        return RenameTagResult(affected_count=affected)


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
    """Merge two tags: ``source`` becomes ``target`` across one user's tracks."""

    async def execute(
        self,
        command: MergeTagsCommand,
        uow: UnitOfWorkProtocol,
    ) -> MergeTagsResult:
        affected = await _bulk_tag_mutation(
            uow,
            lambda repo: repo.merge_tags(
                user_id=command.user_id,
                source=command.source,
                target=command.target,
            ),
        )
        return MergeTagsResult(affected_count=affected)
