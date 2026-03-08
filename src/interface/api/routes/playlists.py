"""Playlist CRUD endpoints.

Each handler is 5-10 lines: parse request → build Command → execute_use_case() → serialize.
All business logic lives in the use cases — this is pure HTTP translation.
"""

from fastapi import APIRouter, Query
from fastapi.responses import Response

from src.application.runner import execute_use_case
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistUseCase,
)
from src.application.use_cases.delete_canonical_playlist import (
    DeleteCanonicalPlaylistCommand,
    DeleteCanonicalPlaylistUseCase,
)
from src.application.use_cases.list_playlists import (
    ListPlaylistsCommand,
    ListPlaylistsUseCase,
)
from src.application.use_cases.read_canonical_playlist import (
    ReadCanonicalPlaylistCommand,
    ReadCanonicalPlaylistUseCase,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
    UpdateCanonicalPlaylistUseCase,
)
from src.domain.entities.track import TrackList
from src.domain.exceptions import NotFoundError
from src.infrastructure.connectors._shared.metric_registry import (
    MetricConfigProviderImpl,
)
from src.interface.api.schemas.common import PaginatedResponse
from src.interface.api.schemas.playlists import (
    BackupPlaylistRequest,
    CreatePlaylistRequest,
    PlaylistDetailSchema,
    PlaylistEntrySchema,
    PlaylistSummarySchema,
    UpdatePlaylistRequest,
    to_playlist_detail,
    to_playlist_summary,
)

router = APIRouter(prefix="/playlists", tags=["playlists"])


@router.get("")
async def list_playlists(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[PlaylistSummarySchema]:
    """List all playlists with pagination."""
    result = await execute_use_case(
        lambda uow: ListPlaylistsUseCase().execute(ListPlaylistsCommand(), uow)
    )

    # In-memory pagination (playlist count is small)
    playlists = result.playlists[offset : offset + limit]
    return PaginatedResponse(
        data=[to_playlist_summary(p) for p in playlists],
        total=result.total_count,
        limit=limit,
        offset=offset,
    )


@router.post("", status_code=201)
async def create_playlist(body: CreatePlaylistRequest) -> PlaylistDetailSchema:
    """Create a new empty playlist."""
    command = CreateCanonicalPlaylistCommand(
        name=body.name,
        description=body.description,
    )
    result = await execute_use_case(
        lambda uow: CreateCanonicalPlaylistUseCase(
            metric_config=MetricConfigProviderImpl()
        ).execute(command, uow)
    )
    return to_playlist_detail(result.playlist)


@router.post("/backup", status_code=201)
async def backup_playlist(body: BackupPlaylistRequest) -> PlaylistDetailSchema:
    """Backup a playlist from a connector service to the local database."""
    from src.application.services.playlist_backup_service import run_playlist_backup

    result = await run_playlist_backup(
        connector_name=body.connector, playlist_id=body.playlist_id
    )
    return to_playlist_detail(result.playlist)


@router.get("/{playlist_id}")
async def get_playlist(playlist_id: int) -> PlaylistDetailSchema:
    """Get a playlist by ID with all entries."""
    command = ReadCanonicalPlaylistCommand(playlist_id=str(playlist_id))
    result = await execute_use_case(
        lambda uow: ReadCanonicalPlaylistUseCase().execute(command, uow)
    )
    if result.playlist is None:
        raise NotFoundError(f"Playlist {playlist_id} not found")
    return to_playlist_detail(result.playlist)


@router.patch("/{playlist_id}")
async def update_playlist(
    playlist_id: int, body: UpdatePlaylistRequest
) -> PlaylistDetailSchema:
    """Update playlist metadata (name and/or description)."""
    command = UpdateCanonicalPlaylistCommand(
        playlist_id=str(playlist_id),
        new_tracklist=TrackList(),
        playlist_name=body.name,
        playlist_description=body.description,
    )
    result = await execute_use_case(
        lambda uow: UpdateCanonicalPlaylistUseCase(
            metric_config=MetricConfigProviderImpl()
        ).execute(command, uow)
    )
    return to_playlist_detail(result.playlist)


@router.delete("/{playlist_id}", status_code=204)
async def delete_playlist(playlist_id: int) -> Response:
    """Delete a playlist by ID."""
    command = DeleteCanonicalPlaylistCommand(
        playlist_id=str(playlist_id), force_delete=True
    )
    await execute_use_case(
        lambda uow: DeleteCanonicalPlaylistUseCase().execute(command, uow)
    )
    return Response(status_code=204)


@router.get("/{playlist_id}/tracks")
async def get_playlist_tracks(
    playlist_id: int,
    limit: int = Query(default=10000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[PlaylistEntrySchema]:
    """Get paginated track entries for a playlist."""
    command = ReadCanonicalPlaylistCommand(playlist_id=str(playlist_id))
    result = await execute_use_case(
        lambda uow: ReadCanonicalPlaylistUseCase().execute(command, uow)
    )
    if result.playlist is None:
        raise NotFoundError(f"Playlist {playlist_id} not found")

    from src.interface.api.schemas.playlists import to_playlist_entry

    entries = result.playlist.entries
    page = entries[offset : offset + limit]
    return PaginatedResponse(
        data=[to_playlist_entry(entry, offset + idx) for idx, entry in enumerate(page)],
        total=len(entries),
        limit=limit,
        offset=offset,
    )
