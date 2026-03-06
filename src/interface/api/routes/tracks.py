"""Track library and detail endpoints.

Merged search+list on GET /tracks (query param `q` triggers search).
All filtering, sorting, and pagination is server-side.
"""

from fastapi import APIRouter, Query

from src.application.runner import execute_use_case
from src.application.use_cases.get_track_details import GetTrackDetailsUseCase
from src.application.use_cases.list_tracks import ListTracksCommand, ListTracksUseCase
from src.config.constants import BusinessLimits
from src.domain.entities import Playlist
from src.domain.repositories.interfaces import UnitOfWorkProtocol
from src.interface.api.schemas.common import PaginatedResponse
from src.interface.api.schemas.tracks import (
    LibraryTrackSchema,
    PlaylistBriefSchema,
    TrackDetailSchema,
    playlist_to_brief_schema,
    to_library_track,
    to_track_detail,
)

router = APIRouter(prefix="/tracks", tags=["tracks"])


@router.get("")
async def list_tracks(
    q: str | None = Query(
        default=None,
        min_length=BusinessLimits.MIN_SEARCH_LENGTH,
        description="Search title/artist/album",
    ),
    liked: bool | None = Query(default=None, description="Filter by liked status"),
    connector: str | None = Query(default=None, description="Filter by connector"),
    sort: str = Query(default="title_asc", description="Sort field and direction"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[LibraryTrackSchema]:
    """List tracks with optional search, filters, sorting, and pagination."""
    command = ListTracksCommand(
        query=q,
        liked=liked,
        connector=connector,
        sort_by=sort,
        limit=limit,
        offset=offset,
    )
    result = await execute_use_case(
        lambda uow: ListTracksUseCase().execute(command, uow)
    )
    return PaginatedResponse(
        data=[
            to_library_track(t, liked_track_ids=result.liked_track_ids)
            for t in result.tracks
        ],
        total=result.total,
        limit=result.limit,
        offset=result.offset,
    )


@router.get("/{track_id}")
async def get_track_detail(track_id: int) -> TrackDetailSchema:
    """Get full track details with metadata, likes, plays, and playlist memberships."""
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(track_id, uow)
    )
    return to_track_detail(result)


@router.get("/{track_id}/playlists")
async def get_track_playlists(track_id: int) -> list[PlaylistBriefSchema]:
    """Get playlists containing a specific track."""
    playlists = await execute_use_case(
        lambda uow: _get_playlists_for_track(uow, track_id)
    )
    return [playlist_to_brief_schema(p) for p in playlists]


async def _get_playlists_for_track(
    uow: UnitOfWorkProtocol, track_id: int
) -> list[Playlist]:
    """Fetch playlists for a track with existence check."""
    async with uow:
        # Verify track exists (raises NotFoundError if not)
        await uow.get_track_repository().get_by_id(track_id)
        return await uow.get_playlist_repository().get_playlists_for_track(track_id)
