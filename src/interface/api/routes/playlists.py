"""Playlist CRUD endpoints + connector link management.

Each handler is 5-10 lines: parse request -> build Command -> execute_use_case() -> serialize.
All business logic lives in the use cases — this is pure HTTP translation.
"""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from src.application.runner import execute_use_case
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistUseCase,
)
from src.application.use_cases.create_playlist_link import (
    CreatePlaylistLinkCommand,
    CreatePlaylistLinkUseCase,
)
from src.application.use_cases.delete_canonical_playlist import (
    DeleteCanonicalPlaylistCommand,
    DeleteCanonicalPlaylistUseCase,
)
from src.application.use_cases.delete_playlist_link import (
    DeletePlaylistLinkCommand,
    DeletePlaylistLinkUseCase,
)
from src.application.use_cases.list_playlist_links import (
    ListPlaylistLinksCommand,
    ListPlaylistLinksUseCase,
)
from src.application.use_cases.list_playlists import (
    ListPlaylistsCommand,
    ListPlaylistsUseCase,
)
from src.application.use_cases.preview_playlist_sync import (
    PreviewPlaylistSyncCommand,
    PreviewPlaylistSyncUseCase,
)
from src.application.use_cases.read_canonical_playlist import (
    ReadCanonicalPlaylistCommand,
    ReadCanonicalPlaylistUseCase,
)
from src.application.use_cases.sync_playlist_link import (
    SyncPlaylistLinkCommand,
    SyncPlaylistLinkUseCase,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
    UpdateCanonicalPlaylistUseCase,
)
from src.application.use_cases.update_playlist_link import (
    UpdatePlaylistLinkCommand,
    UpdatePlaylistLinkUseCase,
)
from src.config import get_logger
from src.domain.entities.playlist_link import SyncDirection
from src.domain.entities.track import TrackList
from src.domain.exceptions import NotFoundError
from src.infrastructure.connectors._shared.metric_registry import (
    MetricConfigProviderImpl,
)
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.common import PaginatedResponse
from src.interface.api.schemas.playlists import (
    CreateLinkRequest,
    CreatePlaylistRequest,
    PlaylistDetailSchema,
    PlaylistEntrySchema,
    PlaylistLinkSchema,
    PlaylistSummarySchema,
    SyncLinkRequest,
    SyncPreviewResponse,
    SyncStartedResponse,
    UpdateLinkRequest,
    UpdatePlaylistRequest,
    to_link_schema,
    to_playlist_detail,
    to_playlist_summary,
)
from src.interface.api.services.background import (
    finalize_sse_operation,
    launch_background,
)
from src.interface.api.services.sse_operations import (
    build_terminal_event,
    prepare_sse_operation,
)

logger = get_logger(__name__).bind(service="playlists_api")

router = APIRouter(prefix="/playlists", tags=["playlists"])


# ─── Playlist CRUD ────────────────────────────────────────────────────────────


@router.get("")
async def list_playlists(
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[PlaylistSummarySchema]:
    """List all playlists with pagination."""
    result = await execute_use_case(
        lambda uow: ListPlaylistsUseCase().execute(
            ListPlaylistsCommand(user_id=user_id), uow
        ),
        user_id=user_id,
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
async def create_playlist(
    body: CreatePlaylistRequest,
    user_id: str = Depends(get_current_user_id),
) -> PlaylistDetailSchema:
    """Create a new empty playlist."""
    command = CreateCanonicalPlaylistCommand(
        user_id=user_id,
        name=body.name,
        description=body.description,
    )
    result = await execute_use_case(
        lambda uow: CreateCanonicalPlaylistUseCase(
            metric_config=MetricConfigProviderImpl()
        ).execute(command, uow),
        user_id=user_id,
    )
    return to_playlist_detail(result.playlist)


@router.get("/{playlist_id}")
async def get_playlist(
    playlist_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> PlaylistDetailSchema:
    """Get a playlist by ID with all entries."""
    command = ReadCanonicalPlaylistCommand(
        user_id=user_id, playlist_id=str(playlist_id)
    )
    result = await execute_use_case(
        lambda uow: ReadCanonicalPlaylistUseCase().execute(command, uow),
        user_id=user_id,
    )
    if result.playlist is None:
        raise NotFoundError(f"Playlist {playlist_id} not found")

    # Fetch links for full detail
    link_result = await execute_use_case(
        lambda uow: ListPlaylistLinksUseCase().execute(
            ListPlaylistLinksCommand(user_id=user_id, playlist_id=playlist_id), uow
        ),
        user_id=user_id,
    )
    return to_playlist_detail(result.playlist, links=link_result.links)


@router.patch("/{playlist_id}")
async def update_playlist(
    playlist_id: UUID,
    body: UpdatePlaylistRequest,
    user_id: str = Depends(get_current_user_id),
) -> PlaylistDetailSchema:
    """Update playlist metadata (name and/or description)."""
    command = UpdateCanonicalPlaylistCommand(
        user_id=user_id,
        playlist_id=str(playlist_id),
        new_tracklist=TrackList(),
        playlist_name=body.name,
        playlist_description=body.description,
    )
    result = await execute_use_case(
        lambda uow: UpdateCanonicalPlaylistUseCase(
            metric_config=MetricConfigProviderImpl()
        ).execute(command, uow),
        user_id=user_id,
    )
    return to_playlist_detail(result.playlist)


@router.delete("/{playlist_id}", status_code=204)
async def delete_playlist(
    playlist_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Delete a playlist by ID."""
    command = DeleteCanonicalPlaylistCommand(
        user_id=user_id, playlist_id=str(playlist_id), force_delete=True
    )
    await execute_use_case(
        lambda uow: DeleteCanonicalPlaylistUseCase().execute(command, uow),
        user_id=user_id,
    )
    return Response(status_code=204)


@router.get("/{playlist_id}/tracks")
async def get_playlist_tracks(
    playlist_id: UUID,
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(default=10000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[PlaylistEntrySchema]:
    """Get paginated track entries for a playlist."""
    command = ReadCanonicalPlaylistCommand(
        user_id=user_id, playlist_id=str(playlist_id)
    )
    result = await execute_use_case(
        lambda uow: ReadCanonicalPlaylistUseCase().execute(command, uow),
        user_id=user_id,
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


# ─── Playlist Links ──────────────────────────────────────────────────────────


@router.get("/{playlist_id}/links")
async def list_playlist_links(
    playlist_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> list[PlaylistLinkSchema]:
    """List all connector links for a playlist."""
    result = await execute_use_case(
        lambda uow: ListPlaylistLinksUseCase().execute(
            ListPlaylistLinksCommand(user_id=user_id, playlist_id=playlist_id), uow
        ),
        user_id=user_id,
    )
    return [to_link_schema(link) for link in result.links]


@router.post("/{playlist_id}/links", status_code=201)
async def create_playlist_link(
    playlist_id: UUID,
    body: CreateLinkRequest,
    user_id: str = Depends(get_current_user_id),
) -> PlaylistLinkSchema:
    """Link a playlist to an external service playlist.

    Accepts Spotify URLs, URIs, or raw IDs. Validates that the external
    playlist exists before creating the link.
    """
    command = CreatePlaylistLinkCommand(
        user_id=user_id,
        playlist_id=playlist_id,
        connector=body.connector,
        connector_playlist_id=body.connector_playlist_id,
        sync_direction=SyncDirection(body.sync_direction),
    )
    result = await execute_use_case(
        lambda uow: CreatePlaylistLinkUseCase().execute(command, uow),
        user_id=user_id,
    )
    return to_link_schema(result.link)


@router.delete("/{playlist_id}/links/{link_id}", status_code=204)
async def delete_playlist_link(
    playlist_id: UUID,  # noqa: ARG001
    link_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Unlink a playlist from an external service."""
    await execute_use_case(
        lambda uow: DeletePlaylistLinkUseCase().execute(
            DeletePlaylistLinkCommand(user_id=user_id, link_id=link_id), uow
        ),
        user_id=user_id,
    )
    return Response(status_code=204)


@router.patch("/{playlist_id}/links/{link_id}")
async def update_playlist_link(
    playlist_id: UUID,  # noqa: ARG001
    link_id: UUID,
    body: UpdateLinkRequest,
    user_id: str = Depends(get_current_user_id),
) -> PlaylistLinkSchema:
    """Update a playlist link's sync direction."""
    command = UpdatePlaylistLinkCommand(
        user_id=user_id,
        link_id=link_id,
        sync_direction=SyncDirection(body.sync_direction),
    )
    result = await execute_use_case(
        lambda uow: UpdatePlaylistLinkUseCase().execute(command, uow),
        user_id=user_id,
    )
    return to_link_schema(result.link)


@router.get("/{playlist_id}/links/{link_id}/sync/preview")
async def preview_playlist_sync(
    playlist_id: UUID,  # noqa: ARG001
    link_id: UUID,
    direction_override: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
) -> SyncPreviewResponse:
    """Preview what a sync would change without executing it."""
    override = SyncDirection(direction_override) if direction_override else None
    command = PreviewPlaylistSyncCommand(
        user_id=user_id,
        link_id=link_id,
        direction_override=override,
    )
    result = await execute_use_case(
        lambda uow: PreviewPlaylistSyncUseCase().execute(command, uow),
        user_id=user_id,
    )
    return SyncPreviewResponse(
        tracks_to_add=result.tracks_to_add,
        tracks_to_remove=result.tracks_to_remove,
        tracks_unchanged=result.tracks_unchanged,
        direction=result.direction.value,
        connector_name=result.connector_name,
        playlist_name=result.playlist_name,
        has_comparison_data=result.has_comparison_data,
        safety_flagged=result.safety_flagged,
        safety_message=result.safety_message,
    )


@router.post("/{playlist_id}/links/{link_id}/sync", status_code=202)
async def sync_playlist_link(
    playlist_id: UUID,  # noqa: ARG001
    link_id: UUID,
    body: SyncLinkRequest | None = None,
    user_id: str = Depends(get_current_user_id),
) -> SyncStartedResponse:
    """Start a sync operation for a playlist link.

    Returns immediately with an operation_id. Progress streams via
    GET /operations/{operation_id}/progress (existing SSE endpoint).
    """
    direction_override = None
    confirmed = False
    if body:
        if body.direction_override:
            direction_override = SyncDirection(body.direction_override)
        confirmed = body.confirmed

    operation_id, sse_queue = await prepare_sse_operation()

    launch_background(
        f"playlist_sync_{operation_id}",
        lambda: _execute_sync_background(
            operation_id,
            link_id,
            direction_override,
            sse_queue,
            user_id,
            confirmed=confirmed,
        ),
    )

    return SyncStartedResponse(operation_id=operation_id)


async def _execute_sync_background(
    operation_id: str,
    link_id: UUID,
    direction_override: SyncDirection | None,
    sse_queue: asyncio.Queue[object],
    user_id: str,
    *,
    confirmed: bool = False,
) -> None:
    """Execute playlist link sync in background, pushing SSE events."""
    from asyncio import CancelledError
    import contextlib

    try:
        command = SyncPlaylistLinkCommand(
            user_id=user_id,
            link_id=link_id,
            direction_override=direction_override,
            confirmed=confirmed,
        )
        result = await execute_use_case(
            lambda uow: SyncPlaylistLinkUseCase().execute(command, uow),
            user_id=user_id,
        )

        await sse_queue.put(
            build_terminal_event(
                "evt_final",
                "complete",
                operation_id,
                "COMPLETED",
                tracks_added=result.tracks_added,
                tracks_removed=result.tracks_removed,
            )
        )

    except (CancelledError, Exception) as e:
        error_msg = str(e)[:500] if not isinstance(e, CancelledError) else "Cancelled"
        with contextlib.suppress(CancelledError, Exception):
            await sse_queue.put(
                build_terminal_event(
                    "evt_error",
                    "error",
                    operation_id,
                    "FAILED",
                    error_message=error_msg,
                )
            )

    finally:
        await finalize_sse_operation(operation_id)
