"""Track library and detail endpoints.

Merged search+list on GET /tracks (query param `q` triggers search).
All filtering, sorting, and pagination is server-side.
"""

from fastapi import APIRouter, Query

from src.application.runner import execute_use_case
from src.application.use_cases.get_track_details import (
    GetTrackDetailsCommand,
    GetTrackDetailsUseCase,
)
from src.application.use_cases.get_track_playlists import (
    GetTrackPlaylistsCommand,
    GetTrackPlaylistsUseCase,
)
from src.application.use_cases.list_tracks import ListTracksCommand, ListTracksUseCase
from src.config.constants import BusinessLimits
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
    command = GetTrackDetailsCommand(track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(command, uow)
    )
    return to_track_detail(result)


@router.get("/{track_id}/playlists")
async def get_track_playlists(track_id: int) -> list[PlaylistBriefSchema]:
    """Get playlists containing a specific track."""
    command = GetTrackPlaylistsCommand(track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackPlaylistsUseCase().execute(command, uow)
    )
    return [playlist_to_brief_schema(p) for p in result.playlists]
