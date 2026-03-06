"""Spotify business operations - Complex workflows and orchestration.

This module handles complex business logic for Spotify operations that require
multiple API calls, batch processing, or sophisticated coordination. It uses
the SpotifyAPIClient for individual API calls and delegates differential
playlist synchronization to SpotifyPlaylistSyncOperations.

Key components:
- SpotifyOperations: High-level business workflows
- Playlist creation and management with batch processing
- Bulk track operations with intelligent batching
- Differential playlist sync (delegated to playlist_sync_operations module)
- Complex multi-step operations requiring coordination

The operations layer sits between the thin API client and the connector facade,
providing reusable business logic while maintaining clean separation of concerns.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: Spotify API response data

import asyncio
from datetime import UTC, datetime
from typing import Any, Never

from attrs import define, field

from src.config import get_logger, settings
from src.domain.entities import (
    ConnectorPlaylist,
    ConnectorPlaylistItem,
    ConnectorTrack,
    Playlist,
    Track,
)
from src.domain.playlist import PlaylistOperation
from src.domain.repositories.interfaces import TrackRepositoryProtocol
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient
from src.infrastructure.connectors.spotify.conversions import (
    convert_spotify_playlist_to_connector,
    convert_spotify_track_to_connector,
    extract_spotify_track_uris,
    extract_track_metadata_for_playlist_item,
    parse_spotify_timestamp,
    validate_non_empty,
)
from src.infrastructure.connectors.spotify.models import SpotifyTrack
from src.infrastructure.connectors.spotify.playlist_sync_operations import (
    SpotifyPlaylistSyncOperations,
)

# Get contextual logger for operations
logger = get_logger(__name__).bind(service="spotify_operations")


@define(slots=True)
class SpotifyOperations:
    """Business logic service for complex Spotify operations.

    Handles multi-step workflows, batch processing, and coordination of
    multiple API calls. Delegates differential playlist synchronization
    to SpotifyPlaylistSyncOperations for complex URI resolution and
    operation execution.

    Example:
        >>> client = SpotifyAPIClient()
        >>> operations = SpotifyOperations(client)
        >>> playlist_id = await operations.create_playlist_with_tracks(
        ...     "My Playlist", tracks
        ... )
    """

    client: SpotifyAPIClient = field()
    _sync_ops: SpotifyPlaylistSyncOperations = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize playlist sync operations helper."""
        self._sync_ops = SpotifyPlaylistSyncOperations(self.client)

    # Bulk Track Operations

    async def get_tracks_by_ids(self, track_ids: list[str]) -> dict[str, SpotifyTrack]:
        """Fetch multiple tracks from Spotify with simple bulk batching."""
        if early_return := validate_non_empty(track_ids, {}):
            return early_return

        results: dict[str, SpotifyTrack] = {}

        # Process in batches using Spotify's bulk API (50 tracks per call)
        batch_size = settings.api.spotify.batch_size
        total_batches = (len(track_ids) + batch_size - 1) // batch_size

        logger.info(f"Fetching {len(track_ids)} tracks in {total_batches} batches")

        for i in range(0, len(track_ids), batch_size):
            batch_ids = track_ids[i : i + batch_size]

            try:
                # Use bulk tracks API - single call for up to 50 tracks
                validated_tracks = await self.client.get_tracks_bulk(batch_ids)

                if validated_tracks:
                    for track in validated_tracks:
                        current_id = track.id
                        results[current_id] = track

                        # Handle Spotify relinking: if track has linked_from,
                        # also map the original track ID to this data
                        if track.linked_from:
                            original_id = track.linked_from.id
                            results[original_id] = track
                            logger.debug(
                                f"Relinked track found: {original_id} -> {current_id}"
                            )

            except Exception as e:
                logger.error(
                    f"Failed to fetch batch {i // batch_size + 1}/{total_batches}: {e}"
                )
                continue

            # Brief delay between requests if configured
            if settings.api.spotify.request_delay > 0:
                await asyncio.sleep(settings.api.spotify.request_delay)

        logger.info(f"Retrieved {len(results)}/{len(track_ids)} tracks")
        return results

    async def batch_get_track_info(
        self, tracks: list[Track], **_options: Any
    ) -> dict[int, dict[str, Any]]:
        """Fetch track metadata for multiple tracks using bulk Spotify API."""
        # Extract Spotify IDs from tracks that have mappings
        spotify_mapped = [
            (t, t.connector_track_identifiers.get("spotify"))
            for t in tracks
            if t.id and "spotify" in t.connector_track_identifiers
        ]

        if not spotify_mapped:
            return {}

        # Use existing bulk method
        spotify_ids = [sid for _, sid in spotify_mapped if sid is not None]
        spotify_data = await self.get_tracks_by_ids(spotify_ids)

        # Map back to track.id format expected by enricher (protocol requires dict)
        return {
            track.id: spotify_data[spotify_id].model_dump()
            for track, spotify_id in spotify_mapped
            if spotify_id is not None
            and track.id is not None
            and spotify_id in spotify_data
        }

    # Advanced Playlist Operations

    async def get_playlist_with_all_tracks(self, playlist_id: str) -> ConnectorPlaylist:
        """Fetch a Spotify playlist with all tracks using pagination."""
        # Get initial playlist data (returns validated SpotifyPlaylist)
        playlist = await self.client.get_playlist(playlist_id)
        if playlist is None:
            raise TypeError(f"Invalid playlist response for ID {playlist_id}")

        # Handle pagination to get all tracks
        tracks_page = playlist.tracks
        all_items = list(tracks_page.items)

        # Paginate until we get all tracks
        while tracks_page and tracks_page.next:
            next_page = await self.client.get_next_page(tracks_page)
            if next_page is not None:
                all_items.extend(next_page.items)
                tracks_page = next_page
            else:
                logger.warning("Received invalid tracks data during pagination")
                break

        # Convert basic playlist metadata (needs raw dict for conversion)
        connector_playlist = convert_spotify_playlist_to_connector(
            playlist.model_dump()
        )

        # Process each track item with its metadata
        playlist_items: list[ConnectorPlaylistItem] = []
        for idx, item in enumerate(all_items):
            if item.track is not None:
                track = item.track
                track_dict = track.model_dump()

                # Create ConnectorPlaylistItem with track ID and metadata
                playlist_item = ConnectorPlaylistItem(
                    connector_track_identifier=track.id,
                    position=idx,
                    added_at=item.added_at,
                    added_by_id=item.added_by.id or None,
                    extras={
                        "is_local": item.is_local,
                        **extract_track_metadata_for_playlist_item(track_dict),
                        "added_at": item.added_at,
                        "full_track_data": track_dict,
                    },
                )
                playlist_items.append(playlist_item)

        # Add items to the playlist
        from attrs import evolve

        connector_playlist = evolve(connector_playlist, items=playlist_items)

        return connector_playlist

    async def create_playlist_with_tracks(
        self,
        name: str,
        tracks: list[Track],
        description: str | None = None,
    ) -> str:
        """Create a new Spotify playlist with tracks using batch processing."""

        def _raise_playlist_creation_error() -> Never:
            raise ValueError("Failed to create playlist, received None")

        try:
            # Extract Spotify track URIs
            spotify_track_uris = extract_spotify_track_uris(tracks)

            # Create empty playlist
            logger.info(
                f"Creating Spotify playlist: {name} with {len(spotify_track_uris)} tracks"
            )
            playlist = await self.client.create_playlist(
                name=name, description=description or "", public=False
            )

            if not playlist:
                _raise_playlist_creation_error()

            playlist_id = playlist.id

            # Add tracks in batches if any
            if spotify_track_uris:
                await self._add_tracks_to_playlist_batched(
                    playlist_id, spotify_track_uris
                )

        except Exception as e:
            logger.error(f"Error creating playlist '{name}': {e}")
            raise
        else:
            return playlist_id

    async def update_playlist_content(
        self,
        playlist_id: str,
        playlist: Playlist,
        replace: bool = True,
    ) -> None:
        """Update an existing Spotify playlist with new content."""
        # Extract Spotify track URIs from domain playlist
        spotify_track_uris = extract_spotify_track_uris(playlist.tracks)

        logger.info(
            f"{'Replacing' if replace else 'Appending to'} playlist {playlist_id} "
            + f"with {len(spotify_track_uris)} tracks"
        )

        try:
            if replace:
                await self._replace_playlist_content(playlist_id, spotify_track_uris)
            else:
                await self._add_tracks_to_playlist_batched(
                    playlist_id, spotify_track_uris
                )

        except Exception as e:
            logger.error(f"Error updating playlist {playlist_id}: {e}")
            raise

    async def _replace_playlist_content(
        self, playlist_id: str, track_uris: list[str]
    ) -> None:
        """Replace entire playlist contents with new tracks."""
        large_batch_size = settings.api.spotify_large_batch_size

        if track_uris:
            # Replace with first batch
            first_batch = track_uris[:large_batch_size]
            _ = await self.client.playlist_replace_items(playlist_id, first_batch)

            # Add remaining tracks in batches
            remaining_tracks = track_uris[large_batch_size:]
            if remaining_tracks:
                await self._add_tracks_to_playlist_batched(
                    playlist_id, remaining_tracks
                )
        else:
            # Clear playlist if no tracks
            _ = await self.client.playlist_replace_items(playlist_id, [])

    async def _add_tracks_to_playlist_batched(
        self, playlist_id: str, track_uris: list[str]
    ) -> None:
        """Add tracks to playlist using simple bulk batching."""
        if not track_uris:
            return

        large_batch_size = settings.api.spotify_large_batch_size
        total_batches = (len(track_uris) + large_batch_size - 1) // large_batch_size

        logger.info(
            f"Adding {len(track_uris)} tracks to playlist in {total_batches} batches"
        )

        for i in range(0, len(track_uris), large_batch_size):
            batch_uris = track_uris[i : i + large_batch_size]

            try:
                _ = await self.client.playlist_add_items(
                    playlist_id=playlist_id, items=batch_uris
                )
                logger.debug(f"Added batch {i // large_batch_size + 1}/{total_batches}")

            except Exception as e:
                logger.error(
                    f"Failed to add batch {i // large_batch_size + 1}/{total_batches}: {e}"
                )
                continue

            # Brief delay between requests if configured
            if settings.api.spotify.request_delay > 0:
                await asyncio.sleep(settings.api.spotify.request_delay)

    # Differential Playlist Operations

    async def execute_playlist_operations(
        self,
        playlist_id: str,
        operations: list[PlaylistOperation],
        snapshot_id: str | None = None,
        track_repo: TrackRepositoryProtocol | None = None,
    ) -> str | None:
        """Execute a list of differential playlist operations.

        Delegates to SpotifyPlaylistSyncOperations for complex sync logic
        including canonical URI resolution and operation execution.
        """
        return await self._sync_ops.execute_playlist_operations(
            playlist_id, operations, snapshot_id, track_repo
        )

    # User Library Operations

    async def get_liked_tracks_paginated(
        self, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[ConnectorTrack], str | None]:
        """Fetch user's saved/liked tracks with pagination support."""
        logger.info(f"Fetching liked tracks, limit={limit}, cursor={cursor}")

        try:
            # Convert cursor to offset
            offset = 0
            if cursor:
                try:
                    offset = int(cursor)
                except ValueError:
                    logger.warning(f"Invalid cursor format: {cursor}, using offset=0")

            saved_tracks = await self.client.get_saved_tracks(
                limit=min(limit, 50), offset=offset
            )

            if not saved_tracks or "items" not in saved_tracks:
                return [], None

            connector_tracks: list[ConnectorTrack] = []
            for item in saved_tracks["items"]:
                if not item or "track" not in item:
                    continue

                spotify_track = item["track"]
                added_at = item.get("added_at")

                connector_track = convert_spotify_track_to_connector(spotify_track)

                # Add liked timestamp to metadata
                if added_at:
                    parsed_time = parse_spotify_timestamp(added_at)
                    if parsed_time:
                        connector_track.raw_metadata["liked_at"] = (
                            parsed_time.isoformat()
                        )
                        connector_track.raw_metadata["is_liked"] = True

                connector_tracks.append(connector_track)

            # Determine next cursor
            next_cursor = None
            if saved_tracks.get("next") and saved_tracks["items"]:
                next_cursor = str(offset + len(saved_tracks["items"]))

        except Exception as e:
            logger.error(f"Error fetching liked tracks: {e}")
            raise
        else:
            return connector_tracks, next_cursor

    # Playlist Metadata Operations

    async def update_playlist_metadata(
        self, playlist_id: str, metadata_updates: dict[str, str]
    ) -> None:
        """Update Spotify playlist metadata (name, description).

        Args:
            playlist_id: Spotify playlist ID
            metadata_updates: Dictionary with 'name' and/or 'description' keys
        """
        if not metadata_updates:
            logger.debug("No metadata updates provided")
            return

        logger.info(
            f"Updating Spotify playlist {playlist_id} metadata",
            updates=metadata_updates,
        )

        try:
            # Extract supported metadata fields
            name = metadata_updates.get("name")
            description = metadata_updates.get("description")

            if name is not None or description is not None:
                await self.client.playlist_change_details(
                    playlist_id=playlist_id, name=name, description=description
                )
                logger.info(
                    f"Successfully updated playlist {playlist_id} metadata",
                    updates=metadata_updates,
                )

        except Exception as e:
            logger.error(f"Error updating playlist metadata: {e}")
            raise

    async def get_playlist_details(self, playlist_id: str) -> dict[str, Any]:
        """Get comprehensive Spotify playlist metadata.

        Args:
            playlist_id: Spotify playlist ID

        Returns:
            Dictionary with playlist metadata

        Raises:
            ValueError: If playlist not found
        """

        def _raise_playlist_not_found_error(playlist_id: str) -> Never:
            raise ValueError(f"Playlist {playlist_id} not found")

        logger.debug(f"Fetching Spotify playlist details for {playlist_id}")

        try:
            playlist_info = await self.client.get_playlist(playlist_id)

            if not playlist_info:
                _raise_playlist_not_found_error(playlist_id)

            # Extract owner information
            owner_name = playlist_info.owner.display_name or playlist_info.owner.id

        except Exception as e:
            logger.error(f"Error fetching playlist details: {e}")
            raise
        else:
            return {
                "id": playlist_info.id,
                "name": playlist_info.name,
                "description": playlist_info.description or "",
                "owner_name": owner_name,
                "owner_id": playlist_info.owner.id,
                "is_public": playlist_info.public or False,
                "collaborative": playlist_info.collaborative,
                "follower_count": playlist_info.followers.total
                if playlist_info.followers
                else None,
            }

    # Bulk Operations Support

    async def append_tracks_to_playlist(
        self, playlist_id: str, tracks: list[Track]
    ) -> dict[str, Any]:
        """Append tracks to an existing Spotify playlist with metadata tracking.

        Args:
            playlist_id: Spotify playlist ID
            tracks: List of tracks to append

        Returns:
            Dictionary with operation metadata
        """
        spotify_track_uris = extract_spotify_track_uris(tracks)

        if early_return := validate_non_empty(
            spotify_track_uris,
            {
                "tracks_added": 0,
                "api_calls_made": 0,
                "snapshot_id": None,
            },
        ):
            logger.warning("No valid Spotify tracks to append")
            return early_return

        logger.info(
            f"Appending {len(spotify_track_uris)} tracks to playlist {playlist_id}"
        )

        try:
            # Add tracks using batched method
            await self._add_tracks_to_playlist_batched(playlist_id, spotify_track_uris)

            # Calculate API calls made
            large_batch_size = settings.api.spotify_large_batch_size
            api_calls_made = (
                len(spotify_track_uris) + large_batch_size - 1
            ) // large_batch_size

            # Get updated playlist info for snapshot_id
            playlist_info = await self.client.get_playlist(playlist_id)
            api_calls_made += 1

            return {
                "tracks_added": len(spotify_track_uris),
                "api_calls_made": api_calls_made,
                "snapshot_id": playlist_info.snapshot_id if playlist_info else None,
                "last_modified": datetime.now(UTC).isoformat(),
            }

        except Exception as e:
            logger.error(f"Error appending tracks to playlist: {e}")
            raise
