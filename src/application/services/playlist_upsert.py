"""Shared command builders and orchestrator for playlist create-or-update logic.

Eliminates duplication between playlist_backup_service.py (direct UoW execution)
and source_nodes.py (workflow context execution). Both construct identical commands;
the shared builders DRY that up while each caller retains its own execution model.
"""

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistResult,
    CreateCanonicalPlaylistUseCase,
)
from src.application.use_cases.read_canonical_playlist import (
    ReadCanonicalPlaylistCommand,
    ReadCanonicalPlaylistUseCase,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
    UpdateCanonicalPlaylistResult,
    UpdateCanonicalPlaylistUseCase,
)
from src.config import get_logger
from src.domain.entities.playlist import ConnectorPlaylist, Playlist
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


def build_create_playlist_command(
    connector_playlist: ConnectorPlaylist,
    connector_name: str,
    playlist_id: str,
) -> CreateCanonicalPlaylistCommand:
    """Build create command from connector playlist data."""
    return CreateCanonicalPlaylistCommand(
        name=connector_playlist.name,
        tracklist=TrackList(),
        connector_playlist=connector_playlist,
        connector_name=connector_name,
        connector_id=playlist_id,
        description=connector_playlist.description or f"Imported from {connector_name}",
    )


def build_update_playlist_command(
    existing_playlist: Playlist,
    connector_playlist: ConnectorPlaylist,
    connector_name: str,
) -> UpdateCanonicalPlaylistCommand:
    """Build update command from existing playlist + connector data."""
    return UpdateCanonicalPlaylistCommand(
        playlist_id=str(existing_playlist.id),
        new_tracklist=TrackList(),
        connector_playlist=connector_playlist,
        playlist_name=connector_playlist.name,
        playlist_description=connector_playlist.description
        or f"Updated from {connector_name}",
    )


async def upsert_canonical_playlist(
    connector_playlist: ConnectorPlaylist,
    connector_name: str,
    playlist_id: str,
    uow: UnitOfWorkProtocol,
) -> CreateCanonicalPlaylistResult | UpdateCanonicalPlaylistResult:
    """Full create-or-update flow for callers with direct UoW access.

    Reads existing playlist, builds appropriate command, executes use case.
    Used by playlist_backup_service and source_nodes for atomic sync+upsert flows.
    """
    # Check if playlist already exists locally
    existing_playlist = None
    try:
        read_use_case = ReadCanonicalPlaylistUseCase()
        read_command = ReadCanonicalPlaylistCommand(
            playlist_id=playlist_id, connector=connector_name
        )
        result = await read_use_case.execute(read_command, uow)
        existing_playlist = result.playlist
        if existing_playlist:
            logger.info(
                "Found existing local playlist",
                local_id=existing_playlist.id,
                name=existing_playlist.name,
            )
    except ValueError:
        logger.info("No existing local playlist found - will create new one")

    if existing_playlist:
        command = build_update_playlist_command(
            existing_playlist, connector_playlist, connector_name
        )
        result = await UpdateCanonicalPlaylistUseCase().execute(command, uow)
        logger.info(
            "Updated existing playlist",
            playlist_id=result.playlist.id,
            operations=result.operations_performed,
            tracks_added=result.tracks_added,
            tracks_removed=result.tracks_removed,
        )
        return result
    else:
        command = build_create_playlist_command(
            connector_playlist, connector_name, playlist_id
        )
        result = await CreateCanonicalPlaylistUseCase().execute(command, uow)
        logger.info(
            "Created new playlist",
            playlist_id=result.playlist.id,
            name=result.playlist.name,
            tracks_created=result.tracks_created,
        )
        return result
