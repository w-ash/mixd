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
from src.application.use_cases.merge_tracks import (
    MergeTracksCommand,
    MergeTracksUseCase,
)
from src.application.use_cases.relink_connector_track import (
    RelinkConnectorTrackCommand,
    RelinkConnectorTrackUseCase,
)
from src.application.use_cases.set_primary_mapping import (
    SetPrimaryMappingCommand,
    SetPrimaryMappingUseCase,
)
from src.application.use_cases.unlink_connector_track import (
    UnlinkConnectorTrackCommand,
    UnlinkConnectorTrackUseCase,
)
from src.config.constants import BusinessLimits
from src.interface.api.schemas.common import PaginatedResponse
from src.interface.api.schemas.tracks import (
    LibraryTrackSchema,
    MergeTrackRequest,
    PlaylistBriefSchema,
    RelinkMappingRequest,
    TrackDetailSchema,
    UnlinkMappingResponse,
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


@router.post("/{track_id}/merge")
async def merge_track(track_id: int, body: MergeTrackRequest) -> TrackDetailSchema:
    """Merge a duplicate track into this track (winner)."""
    command = MergeTracksCommand(winner_id=track_id, loser_id=body.loser_id)
    await execute_use_case(lambda uow: MergeTracksUseCase().execute(command, uow))
    # Fresh read after merge commit
    detail_cmd = GetTrackDetailsCommand(track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(detail_cmd, uow)
    )
    return to_track_detail(result)


@router.patch("/{track_id}/mappings/{mapping_id}")
async def relink_mapping(
    track_id: int, mapping_id: int, body: RelinkMappingRequest
) -> TrackDetailSchema:
    """Relink a connector mapping to a different canonical track."""
    command = RelinkConnectorTrackCommand(
        mapping_id=mapping_id,
        new_track_id=body.new_track_id,
        current_track_id=track_id,
    )
    await execute_use_case(
        lambda uow: RelinkConnectorTrackUseCase().execute(command, uow)
    )
    # Fresh read after relink
    detail_cmd = GetTrackDetailsCommand(track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(detail_cmd, uow)
    )
    return to_track_detail(result)


@router.delete("/{track_id}/mappings/{mapping_id}")
async def unlink_mapping(
    track_id: int, mapping_id: int
) -> UnlinkMappingResponse:
    """Unlink a connector mapping from this track."""
    command = UnlinkConnectorTrackCommand(
        mapping_id=mapping_id, current_track_id=track_id
    )
    result = await execute_use_case(
        lambda uow: UnlinkConnectorTrackUseCase().execute(command, uow)
    )
    return UnlinkMappingResponse(
        deleted_mapping_id=result.deleted_mapping_id,
        orphan_track_id=result.orphan_track_id,
    )


@router.patch("/{track_id}/mappings/{mapping_id}/primary")
async def set_primary_mapping(
    track_id: int, mapping_id: int
) -> TrackDetailSchema:
    """Set a mapping as the primary for its connector on this track."""
    command = SetPrimaryMappingCommand(mapping_id=mapping_id, track_id=track_id)
    await execute_use_case(
        lambda uow: SetPrimaryMappingUseCase().execute(command, uow)
    )
    # Fresh read
    detail_cmd = GetTrackDetailsCommand(track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(detail_cmd, uow)
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
