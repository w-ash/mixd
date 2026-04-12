"""Tag listing and autocomplete endpoints.

Tag management (add/remove on a track) lives under ``/tracks/{id}/tags``
in ``routes/tracks.py``. This module only hosts the top-level ``/tags``
surface for autocomplete and vocabulary browsing.
"""

from fastapi import APIRouter, Depends, Query

from src.application.runner import execute_use_case
from src.application.use_cases.list_tags import ListTagsCommand, ListTagsUseCase
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.tracks import TagCountSchema

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
) -> list[TagCountSchema]:
    """List a user's tags with usage counts, sorted by count desc.

    When ``q`` is set, results filter via the GIN trigram index on ``tag``
    for sub-millisecond lookups — powers the tag autocomplete in the UI.
    """
    command = ListTagsCommand(user_id=user_id, query=q, limit=limit)
    result = await execute_use_case(
        lambda uow: ListTagsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return [TagCountSchema(tag=tag, count=count) for tag, count in result.tags]
