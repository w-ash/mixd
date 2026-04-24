"""Shared command builders and orchestrator for playlist create-or-update logic.

The shared builders keep command construction DRY across callers that need
different execution models (direct UoW vs. workflow context) while each
caller retains its own transaction boundary.
"""

from typing import TYPE_CHECKING

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
from src.domain.exceptions import NotFoundError
from src.domain.repositories import UnitOfWorkProtocol

if TYPE_CHECKING:
    from src.application.workflows.protocols import MetricConfigProvider

logger = get_logger(__name__)


def build_create_playlist_command(
    connector_playlist: ConnectorPlaylist,
    connector_name: str,
    playlist_id: str,
    *,
    user_id: str,
) -> CreateCanonicalPlaylistCommand:
    """Build create command from connector playlist data."""
    return CreateCanonicalPlaylistCommand(
        user_id=user_id,
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
    *,
    user_id: str,
) -> UpdateCanonicalPlaylistCommand:
    """Build update command from existing playlist + connector data."""
    return UpdateCanonicalPlaylistCommand(
        user_id=user_id,
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
    metric_config: MetricConfigProvider,
    *,
    user_id: str,
) -> CreateCanonicalPlaylistResult | UpdateCanonicalPlaylistResult:
    """CREATE or UPDATE the canonical Playlist that mirrors the connector
    playlist. Caller owns the UoW and the commit boundary."""
    # Check if playlist already exists locally
    existing_playlist = None
    try:
        read_use_case = ReadCanonicalPlaylistUseCase()
        read_command = ReadCanonicalPlaylistCommand(
            user_id=user_id, playlist_id=playlist_id, connector=connector_name
        )
        result = await read_use_case.execute(read_command, uow)
        existing_playlist = result.playlist
        if existing_playlist:
            logger.info(
                "Found existing local playlist",
                local_id=existing_playlist.id,
                name=existing_playlist.name,
            )
    except NotFoundError:
        logger.info("No existing local playlist found - will create new one")

    if existing_playlist:
        command = build_update_playlist_command(
            existing_playlist, connector_playlist, connector_name, user_id=user_id
        )
        result = await UpdateCanonicalPlaylistUseCase(
            metric_config=metric_config
        ).execute(command, uow)
        logger.info(
            "Updated existing playlist",
            playlist_id=result.playlist.id,
            operations=result.operations_performed,
            tracks_added=result.tracks_added,
            tracks_removed=result.tracks_removed,
        )
        return result
    command = build_create_playlist_command(
        connector_playlist, connector_name, playlist_id, user_id=user_id
    )
    result = await CreateCanonicalPlaylistUseCase(metric_config=metric_config).execute(
        command, uow
    )
    logger.info(
        "Created new playlist",
        playlist_id=result.playlist.id,
        name=result.playlist.name,
        tracks_created=result.tracks_created,
    )
    return result
