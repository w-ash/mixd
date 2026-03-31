"""Track library and detail endpoints.

Merged search+list on GET /tracks (query param `q` triggers search).
All filtering, sorting, and pagination is server-side.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

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
from src.interface.api.deps import get_current_user_id
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
    user_id: str = Depends(get_current_user_id),
    q: str | None = Query(
        default=None,
        min_length=BusinessLimits.MIN_SEARCH_LENGTH,
        description="Search title/artist/album",
    ),
    liked: bool | None = Query(default=None, description="Filter by liked status"),
    connector: str | None = Query(default=None, description="Filter by connector"),
    sort: str = Query(
        default="title_asc",
        description="Sort field and direction",
        pattern="^(title|artist|added|duration)_(asc|desc)$",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(
        default=None, description="Opaque cursor for keyset pagination"
    ),
) -> PaginatedResponse[LibraryTrackSchema]:
    """List tracks with optional search, filters, sorting, and pagination.

    Supports both offset-based and cursor-based (keyset) pagination.
    When ``cursor`` is provided, it takes precedence over ``offset`` for
    O(1) page seeking regardless of depth.
    """
    command = ListTracksCommand(
        user_id=user_id,
        query=q,
        liked=liked,
        connector=connector,
        sort_by=sort,  # type: ignore[arg-type]  # validated by FastAPI regex pattern
        limit=limit,
        offset=offset,
        cursor=cursor,
    )
    result = await execute_use_case(
        lambda uow: ListTracksUseCase().execute(command, uow),
        user_id=user_id,
    )
    return PaginatedResponse(
        data=[
            to_library_track(t, liked_track_ids=result.liked_track_ids)
            for t in result.tracks
        ],
        total=result.total,
        limit=result.limit,
        offset=result.offset,
        next_cursor=result.next_cursor,
    )


@router.get("/{track_id}")
async def get_track_detail(
    track_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> TrackDetailSchema:
    """Get full track details with metadata, likes, plays, and playlist memberships."""
    command = GetTrackDetailsCommand(user_id=user_id, track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return to_track_detail(result)


@router.post("/{track_id}/merge")
async def merge_track(
    track_id: UUID,
    body: MergeTrackRequest,
    user_id: str = Depends(get_current_user_id),
) -> TrackDetailSchema:
    """Merge a duplicate track into this track (winner)."""
    command = MergeTracksCommand(
        user_id=user_id, winner_id=track_id, loser_id=body.loser_id
    )
    await execute_use_case(
        lambda uow: MergeTracksUseCase().execute(command, uow),
        user_id=user_id,
    )
    # Fresh read after merge commit
    detail_cmd = GetTrackDetailsCommand(user_id=user_id, track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(detail_cmd, uow),
        user_id=user_id,
    )
    return to_track_detail(result)


@router.patch("/{track_id}/mappings/{mapping_id}")
async def relink_mapping(
    track_id: UUID,
    mapping_id: UUID,
    body: RelinkMappingRequest,
    user_id: str = Depends(get_current_user_id),
) -> TrackDetailSchema:
    """Relink a connector mapping to a different canonical track."""
    command = RelinkConnectorTrackCommand(
        user_id=user_id,
        mapping_id=mapping_id,
        new_track_id=body.new_track_id,
        current_track_id=track_id,
    )
    await execute_use_case(
        lambda uow: RelinkConnectorTrackUseCase().execute(command, uow),
        user_id=user_id,
    )
    # Fresh read after relink
    detail_cmd = GetTrackDetailsCommand(user_id=user_id, track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(detail_cmd, uow),
        user_id=user_id,
    )
    return to_track_detail(result)


@router.delete("/{track_id}/mappings/{mapping_id}")
async def unlink_mapping(
    track_id: UUID,
    mapping_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> UnlinkMappingResponse:
    """Unlink a connector mapping from this track."""
    command = UnlinkConnectorTrackCommand(
        user_id=user_id, mapping_id=mapping_id, current_track_id=track_id
    )
    result = await execute_use_case(
        lambda uow: UnlinkConnectorTrackUseCase().execute(command, uow),
        user_id=user_id,
    )
    return UnlinkMappingResponse(
        deleted_mapping_id=result.deleted_mapping_id,
        orphan_track_id=result.orphan_track_id,
    )


@router.patch("/{track_id}/mappings/{mapping_id}/primary")
async def set_primary_mapping(
    track_id: UUID,
    mapping_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> TrackDetailSchema:
    """Set a mapping as the primary for its connector on this track."""
    command = SetPrimaryMappingCommand(
        user_id=user_id, mapping_id=mapping_id, track_id=track_id
    )
    await execute_use_case(
        lambda uow: SetPrimaryMappingUseCase().execute(command, uow),
        user_id=user_id,
    )
    # Fresh read
    detail_cmd = GetTrackDetailsCommand(user_id=user_id, track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(detail_cmd, uow),
        user_id=user_id,
    )
    return to_track_detail(result)


@router.get("/{track_id}/playlists")
async def get_track_playlists(
    track_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> list[PlaylistBriefSchema]:
    """Get playlists containing a specific track."""
    command = GetTrackPlaylistsCommand(user_id=user_id, track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackPlaylistsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return [playlist_to_brief_schema(p) for p in result.playlists]
