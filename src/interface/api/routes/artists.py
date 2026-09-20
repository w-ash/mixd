"""Artist browse, detail, favorite and enrichment endpoints.

``GET /artists`` is the merged list+search the artists page loads; the favorite
pair mirrors the track preference pair (POST reports whether anything changed,
DELETE is idempotent and answers 204 either way). Enrichment is a long
operation and returns an ``operation_id`` to subscribe to over SSE.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from src.application.runner import execute_use_case
from src.application.use_cases.favorite_artist import (
    FavoriteArtistCommand,
    FavoriteArtistUseCase,
)
from src.application.use_cases.get_artist_detail import (
    GetArtistDetailCommand,
    GetArtistDetailUseCase,
)
from src.application.use_cases.list_artists import (
    ListArtistsCommand,
    ListArtistsUseCase,
)
from src.domain.repositories.artist import ArtistSortBy
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.artists import (
    ArtistDetailSchema,
    EnrichArtistsRequest,
    FavoriteArtistResponse,
    PaginatedArtistsResponse,
    to_artist_detail,
    to_artist_summary,
)
from src.interface.api.schemas.imports import OperationStartedResponse
from src.interface.api.services.progress import OperationBoundEmitter
from src.interface.api.services.sse_operations import launch_sse_operation

router = APIRouter(prefix="/artists", tags=["artists"])


@router.get("")
async def list_artists(
    user_id: str = Depends(get_current_user_id),
    search: str | None = Query(default=None, description="Substring match on name"),
    favorites_only: bool = Query(
        default=False, description="Only artists the user favorited"
    ),
    sort: Annotated[
        ArtistSortBy, Query(description="Sort field and direction")
    ] = "name_asc",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(
        default=None, description="Opaque cursor for keyset pagination"
    ),
) -> PaginatedArtistsResponse:
    """List artists with optional search, favorites filter, sorting, paging."""
    command = ListArtistsCommand(
        user_id=user_id,
        search=search,
        favorites_only=favorites_only,
        sort_by=sort,
        limit=limit,
        offset=offset,
        cursor=cursor,
    )
    result = await execute_use_case(
        lambda uow: ListArtistsUseCase().execute(command, uow), user_id=user_id
    )
    return PaginatedArtistsResponse(
        data=[to_artist_summary(a, result) for a in result.artists],
        total=result.total,
        limit=result.limit,
        offset=result.offset,
        next_cursor=result.next_cursor,
    )


@router.post("/enrich")
async def enrich_artists(
    body: EnrichArtistsRequest,
    user_id: str = Depends(get_current_user_id),
) -> OperationStartedResponse:
    """Resolve artist identity against MusicBrainz in the background."""

    async def _enrich(emitter: OperationBoundEmitter) -> object:
        from src.application.use_cases.enrich_artists import run_enrich_artists

        enriched = await run_enrich_artists(
            user_id=user_id,
            limit=body.limit,
            refresh_older_than_days=body.refresh_older_than_days,
            progress_emitter=emitter,
        )
        # Unwrap to the OperationResult so the audit path records counts.
        return enriched.result

    return await launch_sse_operation(
        user_id=user_id,
        operation_type="artist_enrichment",
        coro_factory=_enrich,
    )


@router.get("/{artist_id}")
async def get_artist_detail(
    artist_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> ArtistDetailSchema:
    """Get one artist with connector mappings, track count and favorite state."""
    command = GetArtistDetailCommand(user_id=user_id, artist_id=artist_id)
    result = await execute_use_case(
        lambda uow: GetArtistDetailUseCase().execute(command, uow), user_id=user_id
    )
    return to_artist_detail(result)


@router.post("/{artist_id}/favorite")
async def favorite_artist(
    artist_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> FavoriteArtistResponse:
    """Favorite an artist. Repeat calls succeed with ``changed=false``."""
    command = FavoriteArtistCommand(
        user_id=user_id, artist_id=artist_id, is_favorited=True
    )
    result = await execute_use_case(
        lambda uow: FavoriteArtistUseCase().execute(command, uow), user_id=user_id
    )
    return FavoriteArtistResponse.model_validate(result)


@router.delete("/{artist_id}/favorite", status_code=204)
async def unfavorite_artist(
    artist_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Unfavorite an artist. Idempotent — 204 even if it was not favorited."""
    command = FavoriteArtistCommand(
        user_id=user_id, artist_id=artist_id, is_favorited=False
    )
    await execute_use_case(
        lambda uow: FavoriteArtistUseCase().execute(command, uow), user_id=user_id
    )
    return Response(status_code=204)
