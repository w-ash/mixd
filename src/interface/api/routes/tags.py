"""Tag listing, autocomplete, and management endpoints.

Tag management on a single track lives under ``/tracks/{id}/tags`` in
``routes/tracks.py``. This module hosts the top-level ``/tags`` surface:
- Vocabulary listing + autocomplete (GET ``/tags``)
- Tag-level rename / delete / merge (PATCH ``/tags/{tag}``,
  DELETE ``/tags/{tag}``, POST ``/tags/merge``) — bulk operations across
  every track that carries the tag.
"""

from fastapi import APIRouter, Depends, Query

from src.application.runner import execute_use_case
from src.application.use_cases.delete_tag import (
    DeleteTagCommand,
    DeleteTagUseCase,
)
from src.application.use_cases.list_tags import ListTagsCommand, ListTagsUseCase
from src.application.use_cases.merge_tags import (
    MergeTagsCommand,
    MergeTagsUseCase,
)
from src.application.use_cases.rename_tag import (
    RenameTagCommand,
    RenameTagUseCase,
)
from src.domain.entities.tag import parse_tag
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.tracks import (
    MergeTagsRequest,
    RenameTagRequest,
    TagOperationResult,
    TagString,
    TagSummarySchema,
)

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("")
async def list_tags(
    user_id: str = Depends(get_current_user_id),
    q: str | None = Query(
        default=None,
        min_length=1,
        description="Trigram-filtered autocomplete query",
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TagSummarySchema]:
    """List a user's tags with usage counts and last-used timestamps.

    When ``q`` is set, results filter via the GIN trigram index on ``tag``
    for sub-millisecond lookups — powers the tag autocomplete in the UI.
    Without ``q``, returns every tag the user has used (capped at
    ``limit``) sorted by ``track_count`` descending — this is the data
    backing the Tag Management page.
    """
    command = ListTagsCommand(user_id=user_id, query=q, limit=limit)
    result = await execute_use_case(
        lambda uow: ListTagsUseCase().execute(command, uow),
        user_id=user_id,
    )
    rows: list[TagSummarySchema] = []
    for tag, count, last_used_at in result.tags:
        namespace, value = parse_tag(tag)
        rows.append(
            TagSummarySchema(
                tag=tag,
                namespace=namespace,
                value=value,
                track_count=count,
                last_used_at=last_used_at,
            )
        )
    return rows


@router.patch("/{tag}")
async def rename_tag(
    tag: TagString,
    body: RenameTagRequest,
    user_id: str = Depends(get_current_user_id),
) -> TagOperationResult:
    """Rename ``tag`` to ``body.new_tag`` across every track for the user.

    Idempotent on tracks that already carry the new tag — those just
    lose the old tag without creating a duplicate. Returns the number
    of tracks affected.
    """
    command = RenameTagCommand(user_id=user_id, source=tag, target=body.new_tag)
    result = await execute_use_case(
        lambda uow: RenameTagUseCase().execute(command, uow),
        user_id=user_id,
    )
    return TagOperationResult(affected_count=result.affected_count)


@router.delete("/{tag}")
async def delete_tag(
    tag: TagString,
    user_id: str = Depends(get_current_user_id),
) -> TagOperationResult:
    """Bulk-delete ``tag`` from every track for the user.

    Cascades to the tag-event log: events for the deleted tag are also
    removed (the audit trail's subject no longer exists). Returns the
    number of tracks affected.
    """
    command = DeleteTagCommand(user_id=user_id, tag=tag)
    result = await execute_use_case(
        lambda uow: DeleteTagUseCase().execute(command, uow),
        user_id=user_id,
    )
    return TagOperationResult(affected_count=result.affected_count)


@router.post("/merge")
async def merge_tags(
    body: MergeTagsRequest,
    user_id: str = Depends(get_current_user_id),
) -> TagOperationResult:
    """Merge ``source`` tag into ``target`` tag across every track.

    Same semantics as rename — exposed under a separate endpoint so the
    UI can present rename / merge as distinct flows. Tracks already
    carrying ``target`` just lose ``source``. Returns the number of
    tracks affected.
    """
    command = MergeTagsCommand(user_id=user_id, source=body.source, target=body.target)
    result = await execute_use_case(
        lambda uow: MergeTagsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return TagOperationResult(affected_count=result.affected_count)
