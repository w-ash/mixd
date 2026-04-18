"""Connector status endpoints.

Thin route handler that delegates all token checking, refresh logic,
and status determination to the connector_status application service.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from src.application.runner import execute_use_case
from src.application.use_cases.import_connector_playlist_as_canonical import (
    run_import_connector_playlists_as_canonical,
)
from src.application.use_cases.list_spotify_playlists import (
    ListSpotifyPlaylistsCommand,
    ListSpotifyPlaylistsUseCase,
)
from src.domain.entities.playlist import SPOTIFY_CONNECTOR
from src.domain.entities.playlist_link import SyncDirection
from src.infrastructure.connectors._shared.connector_status import (
    get_all_connector_statuses,
)
from src.infrastructure.connectors._shared.token_storage import get_token_storage
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.connectors import (
    ConnectorStatusSchema,
    ImportFailureSchema,
    ImportOutcomeSchema,
    ImportSpotifyPlaylistsRequest,
    ImportSpotifyPlaylistsResponse,
    SpotifyPlaylistBrowseResponse,
    SpotifyPlaylistBrowseSchema,
)

router = APIRouter(prefix="/connectors", tags=["connectors"])

_CONNECTABLE_SERVICES = {"spotify", "lastfm"}


@router.get("")
async def get_connectors(
    user_id: str = Depends(get_current_user_id),
) -> list[ConnectorStatusSchema]:
    """Get authentication status of all configured connectors."""
    statuses = await get_all_connector_statuses(user_id)
    return [
        ConnectorStatusSchema(
            name=s.name,
            connected=s.connected,
            account_name=s.account_name,
            token_expires_at=s.token_expires_at,
        )
        for s in statuses
    ]


@router.delete("/{service}/token", status_code=204)
async def delete_connector_token(
    service: str,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Remove stored OAuth token for a connector, disconnecting it."""
    if service not in _CONNECTABLE_SERVICES:
        raise HTTPException(status_code=400, detail=f"Cannot disconnect {service}")
    storage = get_token_storage()
    await storage.delete_token(service, user_id)


@router.get("/spotify/playlists")
async def list_spotify_playlists(
    user_id: str = Depends(get_current_user_id),
    force_refresh: bool = Query(
        default=False,
        description="Bypass the DBConnectorPlaylist cache and re-fetch from Spotify",
    ),
) -> SpotifyPlaylistBrowseResponse:
    """List the user's Spotify playlists for the browser dialog.

    Cache-first: reads from ``connector_playlists`` unless ``force_refresh``
    is true. Per-playlist ``import_status`` reflects whether this user has
    already linked the Spotify playlist into Mixd.
    """
    command = ListSpotifyPlaylistsCommand(user_id=user_id, force_refresh=force_refresh)
    result = await execute_use_case(
        lambda uow: ListSpotifyPlaylistsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return SpotifyPlaylistBrowseResponse(
        data=[SpotifyPlaylistBrowseSchema.model_validate(p) for p in result.playlists],
        from_cache=result.from_cache,
        fetched_at=result.fetched_at,
    )


@router.post("/spotify/playlists/import")
async def import_spotify_playlists(
    body: ImportSpotifyPlaylistsRequest,
    user_id: str = Depends(get_current_user_id),
) -> ImportSpotifyPlaylistsResponse:
    """Import a batch of Spotify playlists with a chosen sync direction.

    Non-atomic: one failing playlist doesn't abort the others. Each
    successful import creates a canonical ``Playlist`` + ``PlaylistLink``;
    previously-imported playlists with a known snapshot are short-circuited
    into ``skipped_unchanged``.
    """
    result = await run_import_connector_playlists_as_canonical(
        user_id=user_id,
        connector_name=SPOTIFY_CONNECTOR,
        connector_playlist_ids=body.connector_playlist_ids,
        sync_direction=SyncDirection(body.sync_direction),
    )
    return ImportSpotifyPlaylistsResponse(
        succeeded=[
            ImportOutcomeSchema(
                connector_playlist_identifier=o.connector_playlist_identifier,
                canonical_playlist_id=str(o.canonical_playlist_id),
                resolved=o.resolved,
                unresolved=o.unresolved,
            )
            for o in result.succeeded
        ],
        skipped_unchanged=list(result.skipped_unchanged),
        failed=[
            ImportFailureSchema(
                connector_playlist_identifier=f.connector_playlist_identifier,
                message=f.message,
            )
            for f in result.failed
        ],
    )
