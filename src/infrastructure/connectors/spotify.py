"""Spotify service connector with domain model conversion.

This module provides a connector for the Spotify API using the spotipy library
(https://spotipy.readthedocs.io/) to handle authentication, rate limiting, and
conversion between Spotify objects and domain models.

Key components:
- SpotifyConnector: OAuth-authenticated client with playlist and track operations
- SpotifyMetricResolver: Resolver for Spotify-specific track metrics
- Conversion utilities: Transform Spotify API responses to domain models

The module supports:
- Fetching and creating playlists with detailed track information
- Searching tracks by ISRC or artist/title combinations
- Retrieving user's liked/saved tracks with pagination
- Resolving popularity metrics for ranking operations
"""

import asyncio
from datetime import UTC, datetime
from itertools import groupby
import os
from typing import Any, ClassVar

import attrs
from attrs import define, field
import backoff
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

from src.config import get_logger, resilient_operation, settings
from src.domain.entities import (
    Artist,
    ConnectorPlaylist,
    ConnectorPlaylistItem,
    ConnectorTrack,
    Playlist,
    Track,
)
from src.domain.playlist import PlaylistOperationType
from src.infrastructure.connectors.base_connector import (
    BaseAPIConnector,
    BaseMetricResolver,
    extract_metric,
    register_metrics,
)
from src.infrastructure.connectors.protocols import ConnectorConfig
from src.infrastructure.connectors.spotify_error_classifier import (
    SpotifyErrorClassifier,
)
from src.infrastructure.connectors.track_conversion_registry import (
    register_track_converter,
)

# Get contextual logger with service binding
logger = get_logger(__name__).bind(service="spotify")


def _extract_spotify_track_uris(tracks: list[Track]) -> list[str]:
    """Extract Spotify track URIs from domain tracks.

    Args:
        tracks: List of domain Track objects

    Returns:
        List of Spotify track URIs for tracks that have Spotify IDs
    """
    return [
        f"spotify:track:{t.connector_track_ids['spotify']}"
        for t in tracks
        if "spotify" in t.connector_track_ids
    ]


async def _spotify_api_call(client, method_name: str, *args, **kwargs):
    """Execute Spotify API call with consistent error handling.

    Args:
        client: Spotify client instance
        method_name: Name of the method to call
        *args, **kwargs: Arguments to pass to the method

    Returns:
        API response
    """
    method = getattr(client, method_name)
    return await asyncio.to_thread(method, *args, **kwargs)


def _validate_non_empty(items: list, empty_result=None):
    """Validate that items list is not empty and return early result if empty.

    Args:
        items: List to validate
        empty_result: Result to return if items is empty

    Returns:
        empty_result if items is empty, None otherwise
    """
    return empty_result if not items else None


load_dotenv()

os.environ["SPOTIPY_CLIENT_ID"] = os.getenv("SPOTIFY_CLIENT_ID", "")
os.environ["SPOTIPY_CLIENT_SECRET"] = os.getenv("SPOTIFY_CLIENT_SECRET", "")
os.environ["SPOTIPY_REDIRECT_URI"] = os.getenv("SPOTIFY_REDIRECT_URI", "")


@define(slots=True)
class SpotifyConnector(BaseAPIConnector):
    """Thin wrapper around spotipy with domain model conversion.

    Handles OAuth flow and provides methods to:
    - Fetch playlists with track details
    - Create new playlists from domain models
    - Update existing playlists

    All methods handle rate limiting via backoff decorator.
    """

    client: spotipy.Spotify = field(init=False, repr=False)

    @property
    def connector_name(self) -> str:
        """The name of this connector."""
        return "spotify"

    @property
    def market(self) -> str:
        """Get configured Spotify market."""
        return settings.api.spotify_market

    @property
    def error_classifier(self):
        """Get Spotify-specific error classifier."""
        return SpotifyErrorClassifier()

    def __attrs_post_init__(self) -> None:
        """Initialize Spotify client with OAuth configuration and timeout settings."""
        logger.debug("Initializing Spotify connector")
        self.client = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                scope=[
                    "playlist-modify-public",
                    "playlist-modify-private",
                    "playlist-read-private",
                    "playlist-read-collaborative",
                    "user-library-read",
                ],
                open_browser=True,
                cache_handler=spotipy.CacheFileHandler(cache_path=".spotify_cache"),
            ),
            requests_timeout=int(self.get_connector_config("REQUEST_TIMEOUT") or 15),
            retries=int(self.get_connector_config("RETRIES") or 5),
        )

    @resilient_operation("get_spotify_tracks_by_ids")
    @backoff.on_exception(backoff.expo, spotipy.SpotifyException, max_tries=3)
    async def get_tracks_by_ids(
        self, track_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Fetch multiple tracks from Spotify in bulk (up to 50 per request).

        Args:
            track_ids: List of Spotify track IDs

        Returns:
            Dictionary mapping track IDs to their data
        """
        if early_return := _validate_non_empty(track_ids, {}):
            return early_return

        results = {}

        async def process_batch(batch_ids: list[str]) -> dict[str, dict[str, Any]]:
            """Process a single batch of track IDs."""
            batch_results = {}
            
            try:
                # Use bulk tracks API - single call for up to 50 tracks
                tracks_response = await _spotify_api_call(
                    self.client, "tracks", batch_ids, market=self.market
                )

                if tracks_response and "tracks" in tracks_response:
                    for track in tracks_response["tracks"]:
                        if track and "id" in track:
                            current_id = track["id"]
                            batch_results[current_id] = track

                            # Handle Spotify relinking: if track has linked_from,
                            # also map the original track ID to this data
                            linked_from = track.get("linked_from")
                            if linked_from and "id" in linked_from:
                                original_id = linked_from["id"]
                                batch_results[original_id] = track
                                logger.debug(
                                    f"Relinked track found: {original_id} -> {current_id}"
                                )
                        else:
                            logger.warning("Received null track in batch response")

            except Exception as e:
                logger.error(f"Failed to fetch track batch: {e}")
                # Return empty dict for failed batch

            return batch_results

        # Process using unified BatchProcessor
        batch_results = await self.batch_processor.process(
            items=[track_ids[i:i + settings.api.spotify_batch_size] 
                   for i in range(0, len(track_ids), settings.api.spotify_batch_size)],
            process_func=process_batch,
            progress_description="Fetching Spotify track metadata"
        )

        # Merge all batch results
        for batch_result in batch_results:
            results.update(batch_result)

        logger.info(
            f"Retrieved {len(results)}/{len(track_ids)} tracks using unified batch processor"
        )
        return results

    async def batch_get_track_info(
        self, tracks: list[Track], **_options: Any
    ) -> dict[int, dict[str, Any]]:
        """Fetch track metadata for multiple tracks using bulk Spotify API.

        Args:
            tracks: List of domain Track objects.
            **_options: Additional options (unused).

        Returns:
            Dictionary mapping track.id to Spotify track metadata.
        """
        # Extract Spotify IDs from tracks that have mappings
        spotify_mapped = [
            (t, t.connector_track_ids.get("spotify"))
            for t in tracks
            if t.id and "spotify" in t.connector_track_ids
        ]

        if not spotify_mapped:
            return {}

        # Use existing bulk method
        spotify_ids = [sid for _, sid in spotify_mapped if sid is not None]
        spotify_data = await self.get_tracks_by_ids(spotify_ids)

        # Map back to track.id format expected by enricher
        return {
            track.id: spotify_data[spotify_id]
            for track, spotify_id in spotify_mapped
            if spotify_id is not None
            and track.id is not None
            and spotify_id in spotify_data
        }

    @resilient_operation("search_spotify_by_isrc")
    @backoff.on_exception(backoff.expo, spotipy.SpotifyException, max_tries=3)
    async def search_by_isrc(self, isrc: str) -> dict[str, Any] | None:
        """Search for a track using ISRC identifier.

        Args:
            isrc: The ISRC code to search for

        Returns:
            Track data if found, None otherwise
        """
        logger.debug(f"Searching Spotify for ISRC: {isrc}")
        results = await asyncio.to_thread(
            self.client.search,
            f"isrc:{isrc}",
            type="track",
            limit=1,
            market=self.market,
        )

        tracks = results.get("tracks", {}).get("items", []) if results else []
        if not tracks:
            logger.warning("Spotify search by ISRC returned no results", isrc=isrc)
        return tracks[0] if tracks else None

    @resilient_operation("search_spotify_track")
    @backoff.on_exception(backoff.expo, spotipy.SpotifyException, max_tries=3)
    async def search_track(self, artist: str, title: str) -> dict[str, Any] | None:
        """Search for a track by artist and title.

        Args:
            artist: Artist name
            title: Track title

        Returns:
            Track data if found, None otherwise
        """
        query = f"artist:{artist} track:{title}"
        logger.debug(f"Searching Spotify with query: {query}")
        results = await asyncio.to_thread(
            self.client.search,
            query,
            type="track",
            limit=1,
            market=self.market,
        )

        tracks = results.get("tracks", {}).get("items", []) if results else []
        return tracks[0] if tracks else None

    @resilient_operation("get_spotify_playlist")
    @backoff.on_exception(backoff.expo, spotipy.SpotifyException, max_tries=3)
    async def get_spotify_playlist(self, playlist_id: str) -> ConnectorPlaylist:
        """Fetch a Spotify playlist with its tracks.

        Args:
            playlist_id: Spotify playlist ID to fetch

        Returns:
            ConnectorPlaylist containing playlist metadata and track items
        """
        # Get initial playlist data
        raw_playlist = await asyncio.to_thread(
            self.client.playlist,
            playlist_id,
            market=self.market,
        )

        if not isinstance(raw_playlist, dict):
            raise ValueError(f"Invalid playlist response for ID {playlist_id}")

        # Handle pagination to get all tracks
        tracks = raw_playlist["tracks"]
        all_items = tracks["items"]

        # Paginate until we get all tracks
        while tracks["next"]:
            tracks = await asyncio.to_thread(self.client.next, tracks)
            if tracks is not None and "items" in tracks:
                all_items.extend(tracks["items"])
            else:
                logger.warning("Received invalid tracks data during pagination")
                break

        # Convert basic playlist metadata
        connector_playlist = convert_spotify_playlist_to_connector(raw_playlist)

        # Process each track item with its metadata
        playlist_items = []
        for idx, item in enumerate(all_items):
            if item.get("track") is not None:
                track = item["track"]
                # Get the added_at timestamp
                added_at = item.get("added_at")

                # Create ConnectorPlaylistItem with track ID and metadata
                playlist_item = ConnectorPlaylistItem(
                    connector_track_id=track["id"],
                    position=idx,
                    added_at=added_at,
                    added_by_id=item.get("added_by", {}).get("id"),
                    extras={
                        "is_local": item.get("is_local", False),
                        "track_name": track.get(
                            "name"
                        ),  # Include minimal track info for reference
                        "artist_names": [a["name"] for a in track.get("artists", [])],
                        # Store the added_at in extras too for easy access
                        "added_at": added_at,
                    },
                )
                playlist_items.append(playlist_item)

        # Use evolve to add items to the playlist
        connector_playlist = attrs.evolve(connector_playlist, items=playlist_items)

        return connector_playlist

    @resilient_operation("create_spotify_playlist")
    @backoff.on_exception(backoff.expo, spotipy.SpotifyException, max_tries=3)
    async def create_playlist(
        self,
        name: str,
        tracks: list[Track],
        description: str | None = None,
    ) -> str:
        """Create a new Spotify playlist with tracks.

        Args:
            name: Playlist name
            tracks: List of tracks to add
            description: Optional playlist description

        Returns:
            Spotify playlist ID
        """
        try:
            # Extract Spotify track URIs
            spotify_track_uris = _extract_spotify_track_uris(tracks)

            # Create empty playlist
            logger.info(
                f"Creating Spotify playlist: {name} with {len(spotify_track_uris)} tracks",
            )
            playlist = await asyncio.to_thread(
                self.client.user_playlist_create,
                user=(self.client.me() or {}).get("id", ""),
                name=name,
                public=False,
                description=description or "",
            )

            # Add tracks in batches (Spotify API limits)
            if spotify_track_uris:
                playlist_id = playlist["id"] if playlist else ""
                large_batch_size = settings.api.spotify_large_batch_size

                async def add_batch(batch_uris: list[str]) -> None:
                    """Add a batch of track URIs to the playlist."""
                    await _spotify_api_call(
                        self.client,
                        "playlist_add_items",
                        playlist_id=playlist_id,
                        items=batch_uris,
                    )

                # Process using unified BatchProcessor
                await self.batch_processor.process(
                    items=[spotify_track_uris[i:i + large_batch_size] 
                           for i in range(0, len(spotify_track_uris), large_batch_size)],
                    process_func=add_batch,
                    progress_description="Adding tracks to new playlist"
                )

            if playlist is not None:
                return playlist["id"]
            else:
                raise ValueError("Failed to create playlist, received None")
        except spotipy.SpotifyException as e:
            logger.error(f"Spotify API error: {e}")
            raise

    @resilient_operation("update_spotify_playlist")
    @backoff.on_exception(backoff.expo, spotipy.SpotifyException, max_tries=3)
    async def update_playlist(
        self,
        playlist_id: str,
        playlist: Playlist,
        replace: bool = True,
    ) -> None:
        """Update an existing Spotify playlist.

        Args:
            playlist_id: Spotify ID of the playlist to update
            playlist: Domain playlist with the tracks to use
            replace: If True, replace all tracks; if False, append
        """
        # Extract Spotify track URIs from domain playlist
        spotify_track_uris = _extract_spotify_track_uris(playlist.tracks)

        logger.info(
            f"{'Replacing' if replace else 'Appending to'} playlist {playlist_id} "
            f"with {len(spotify_track_uris)} tracks",
        )

        try:
            large_batch_size = settings.api.spotify_large_batch_size

            if replace:
                # Replace entire playlist contents with first batch
                first_batch = (
                    spotify_track_uris[:large_batch_size] if spotify_track_uris else []
                )
                await asyncio.to_thread(
                    self.client.playlist_replace_items,
                    playlist_id=playlist_id,
                    items=first_batch,
                )

                # If we have more tracks, add them in batches
                remaining_tracks = (
                    spotify_track_uris[large_batch_size:]
                    if len(spotify_track_uris) > large_batch_size
                    else []
                )
            else:
                # When appending, start with all tracks
                remaining_tracks = spotify_track_uris

            # Add remaining tracks in bulk batches
            if remaining_tracks:
                async def add_batch(batch_uris: list[str]) -> None:
                    """Add a batch of track URIs to the playlist."""
                    await _spotify_api_call(
                        self.client,
                        "playlist_add_items",
                        playlist_id=playlist_id,
                        items=batch_uris,
                    )

                # Process using unified BatchProcessor
                await self.batch_processor.process(
                    items=[remaining_tracks[i:i + large_batch_size] 
                           for i in range(0, len(remaining_tracks), large_batch_size)],
                    process_func=add_batch,
                    progress_description="Adding remaining tracks to playlist"
                )

        except spotipy.SpotifyException as e:
            logger.error(f"Spotify API error: {e}")
            raise

    @resilient_operation("execute_spotify_playlist_operations")
    @backoff.on_exception(backoff.expo, spotipy.SpotifyException, max_tries=3)
    async def execute_playlist_operations(
        self,
        playlist_id: str,
        operations: list,
        snapshot_id: str | None = None,
    ) -> str | None:
        """Execute a list of differential playlist operations.

        Args:
            playlist_id: Spotify playlist ID
            operations: List of PlaylistOperation objects to execute
            snapshot_id: Optional snapshot ID for conflict detection

        Returns:
            New snapshot ID after operations, None if no operations performed
        """
        if not operations:
            return snapshot_id

        logger.info(
            f"Executing {len(operations)} playlist operations on {playlist_id}",
            snapshot_id=snapshot_id,
        )

        current_snapshot = snapshot_id

        # Process operations in the correct order: remove → add → move

        # Group operations by type using itertools.groupby
        operations_by_type = {
            op_type: list(ops)
            for op_type, ops in groupby(
                sorted(operations, key=lambda op: op.operation_type.value),
                key=lambda op: op.operation_type,
            )
        }
        remove_ops = operations_by_type.get(PlaylistOperationType.REMOVE, [])
        add_ops = operations_by_type.get(PlaylistOperationType.ADD, [])
        move_ops = operations_by_type.get(PlaylistOperationType.MOVE, [])

        try:
            # Step 1: Remove tracks (in reverse order to avoid index shifting)
            if remove_ops:
                current_snapshot = await self._execute_remove_operations(
                    playlist_id, remove_ops, current_snapshot
                )

            # Step 2: Add tracks
            if add_ops:
                current_snapshot = await self._execute_add_operations(
                    playlist_id, add_ops, current_snapshot
                )

            # Step 3: Reorder tracks
            if move_ops:
                current_snapshot = await self._execute_move_operations(
                    playlist_id, move_ops, current_snapshot
                )

            logger.info(
                f"Successfully executed all operations, new snapshot: {current_snapshot}"
            )
            return current_snapshot

        except spotipy.SpotifyException as e:
            logger.error(f"Spotify API error during operations: {e}")
            raise

    async def _execute_remove_operations(
        self,
        playlist_id: str,
        remove_ops: list,
        snapshot_id: str | None,
    ) -> str | None:
        """Execute remove operations, batched by track URI."""
        # Group removes by track URI to optimize API calls
        tracks_to_remove = {}
        for op in remove_ops:
            if op.spotify_uri:
                if op.spotify_uri not in tracks_to_remove:
                    tracks_to_remove[op.spotify_uri] = []
                if op.old_position is not None:
                    tracks_to_remove[op.spotify_uri].append(op.old_position)

        # Execute removes in batches
        items_to_remove = []
        for uri, positions in tracks_to_remove.items():
            if positions:
                # Remove specific positions
                items_to_remove.append({"uri": uri, "positions": positions})
            else:
                # Remove all occurrences
                items_to_remove.append({"uri": uri})

        if items_to_remove:
            # Process in batches of 100
            for i in range(0, len(items_to_remove), 100):
                batch = items_to_remove[i : i + 100]
                result = await asyncio.to_thread(
                    self.client.playlist_remove_specific_occurrences_of_items,
                    playlist_id=playlist_id,
                    items=batch,
                    snapshot_id=snapshot_id,
                )
                snapshot_id = result.get("snapshot_id") if result else snapshot_id
                await asyncio.sleep(settings.api.spotify_request_delay)  # Rate limiting

        return snapshot_id

    async def _execute_add_operations(
        self,
        playlist_id: str,
        add_ops: list,
        snapshot_id: str | None,
    ) -> str | None:
        """Execute add operations in position order."""
        # Sort by position to maintain order
        sorted_adds = sorted(add_ops, key=lambda op: op.position)

        # Group consecutive positions to batch when possible
        for op in sorted_adds:
            if op.spotify_uri:
                await asyncio.to_thread(
                    self.client.playlist_add_items,
                    playlist_id=playlist_id,
                    items=[op.spotify_uri],
                    position=op.position,
                )
                # Spotify doesn't return snapshot_id from add_items, so we refresh
                await asyncio.sleep(settings.api.spotify_request_delay)  # Rate limiting

        # Get updated snapshot ID after all adds
        if sorted_adds:
            playlist_info = await asyncio.to_thread(
                self.client.playlist, playlist_id, fields="snapshot_id"
            )
            snapshot_id = playlist_info.get("snapshot_id") if playlist_info else None

        return snapshot_id

    async def _execute_move_operations(
        self,
        playlist_id: str,
        move_ops: list,
        snapshot_id: str | None,
    ) -> str | None:
        """Execute move operations individually."""
        for op in move_ops:
            if op.old_position is not None:
                result = await asyncio.to_thread(
                    self.client.playlist_reorder_items,
                    playlist_id=playlist_id,
                    range_start=op.old_position,
                    insert_before=op.position,
                    range_length=1,
                    snapshot_id=snapshot_id,
                )
                snapshot_id = result.get("snapshot_id") if result else snapshot_id
                await asyncio.sleep(settings.api.spotify_request_delay)  # Rate limiting

        return snapshot_id

    @resilient_operation("get_liked_tracks")
    @backoff.on_exception(backoff.expo, spotipy.SpotifyException, max_tries=3)
    async def get_liked_tracks(
        self,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[ConnectorTrack], str | None]:
        """Fetch user's saved/liked tracks from Spotify with pagination.

        Args:
            limit: Number of tracks to fetch per page (max 50)
            cursor: Pagination cursor from previous calls

        Returns:
            Tuple of (list of ConnectorTrack models, next cursor or None if done)
        """
        logger.info(
            f"Fetching liked tracks from Spotify, limit={limit}, cursor={cursor}"
        )

        try:
            # Convert cursor to offset if provided
            offset = 0
            if cursor:
                try:
                    offset = int(cursor)
                except ValueError:
                    logger.warning(f"Invalid cursor format: {cursor}, using offset=0")

            # Get saved tracks from Spotify API
            saved_tracks = await asyncio.to_thread(
                self.client.current_user_saved_tracks,
                limit=min(limit, 50),  # Spotify's max limit is 50
                offset=offset,
                market=self.market,
            )

            if not saved_tracks or "items" not in saved_tracks:
                logger.warning("No saved tracks found or invalid response format")
                return [], None

            # Extract actual track objects from the response
            # The API returns items with {added_at, track} structure
            connector_tracks = []

            for item in saved_tracks["items"]:
                if not item or "track" not in item:
                    continue

                spotify_track = item["track"]
                # Save the added_at timestamp in the track metadata
                added_at = item.get("added_at")

                # Create connector track
                connector_track = convert_spotify_track_to_connector(spotify_track)

                if added_at:
                    # Add liked timestamp to connector metadata
                    try:
                        # Spotify timestamp format: "2023-09-21T15:48:56Z"
                        liked_at = datetime.fromisoformat(
                            added_at.replace("Z", "+00:00")
                        )

                        # Update raw metadata with liked information
                        connector_track.raw_metadata["liked_at"] = liked_at.isoformat()
                        connector_track.raw_metadata["is_liked"] = True

                    except ValueError:
                        logger.warning(
                            f"Could not parse added_at timestamp: {added_at}"
                        )

                connector_tracks.append(connector_track)

            # Determine next cursor
            next_cursor = None
            if saved_tracks.get("next") and saved_tracks["items"]:
                next_cursor = str(offset + len(saved_tracks["items"]))

            return connector_tracks, next_cursor

        except spotipy.SpotifyException as e:
            logger.error(f"Error fetching liked tracks: {e}")
            raise
        except Exception as e:
            logger.exception(f"Unexpected error fetching liked tracks: {e}")
            raise

    @resilient_operation("append_tracks_to_spotify_playlist")
    @backoff.on_exception(backoff.expo, spotipy.SpotifyException, max_tries=3)
    async def append_tracks_to_playlist(
        self, playlist_id: str, tracks: list[Track]
    ) -> dict[str, Any]:
        """Append tracks to an existing Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            tracks: List of tracks to append

        Returns:
            Dictionary with operation metadata
        """
        # Extract Spotify track URIs
        spotify_track_uris = _extract_spotify_track_uris(tracks)

        if early_return := _validate_non_empty(
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
            f"Appending {len(spotify_track_uris)} tracks to Spotify playlist {playlist_id}"
        )

        try:
            large_batch_size = settings.api.spotify_large_batch_size
            api_calls_made = 0

            async def append_batch(batch_uris: list[str]) -> None:
                """Append a batch of track URIs to the playlist."""
                await _spotify_api_call(
                    self.client,
                    "playlist_add_items",
                    playlist_id=playlist_id,
                    items=batch_uris,
                )

            # Process using unified BatchProcessor
            await self.batch_processor.process(
                items=[spotify_track_uris[i:i + large_batch_size] 
                       for i in range(0, len(spotify_track_uris), large_batch_size)],
                process_func=append_batch,
                progress_description="Appending tracks to playlist"
            )
            
            api_calls_made = (len(spotify_track_uris) + large_batch_size - 1) // large_batch_size

            # Get updated playlist info for snapshot_id
            playlist_info = await asyncio.to_thread(
                self.client.playlist, playlist_id, fields="snapshot_id"
            )
            api_calls_made += 1

            return {
                "tracks_added": len(spotify_track_uris),
                "api_calls_made": api_calls_made,
                "snapshot_id": playlist_info.get("snapshot_id")
                if playlist_info
                else None,
                "last_modified": datetime.now(UTC).isoformat(),
            }

        except spotipy.SpotifyException as e:
            logger.error(f"Spotify API error appending tracks: {e}")
            raise

    @resilient_operation("update_spotify_playlist_metadata")
    @backoff.on_exception(backoff.expo, spotipy.SpotifyException, max_tries=3)
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
            update_params = {}
            if "name" in metadata_updates:
                update_params["name"] = metadata_updates["name"]
            if "description" in metadata_updates:
                update_params["description"] = metadata_updates["description"]

            if update_params:
                await asyncio.to_thread(
                    self.client.playlist_change_details,
                    playlist_id=playlist_id,
                    **update_params,
                )
                logger.info(
                    f"Successfully updated playlist {playlist_id} metadata",
                    updates=update_params,
                )

        except spotipy.SpotifyException as e:
            logger.error(f"Spotify API error updating playlist metadata: {e}")
            raise

    @resilient_operation("get_spotify_playlist_details")
    @backoff.on_exception(backoff.expo, spotipy.SpotifyException, max_tries=3)
    async def get_playlist_details(self, playlist_id: str) -> dict[str, Any]:
        """Get basic Spotify playlist metadata.

        Args:
            playlist_id: Spotify playlist ID

        Returns:
            Dictionary with playlist metadata (name, description, owner info)
        """
        logger.debug(f"Fetching Spotify playlist details for {playlist_id}")

        try:
            playlist_info = await _spotify_api_call(
                self.client,
                "playlist",
                playlist_id,
                fields="id,name,description,owner.id,owner.display_name,public,collaborative,followers.total",
            )

            if not playlist_info:
                raise ValueError(f"Playlist {playlist_id} not found")

            # Extract owner information
            owner = playlist_info.get("owner", {})
            owner_name = owner.get("display_name") or owner.get("id")

            return {
                "id": playlist_info["id"],
                "name": playlist_info.get("name", ""),
                "description": playlist_info.get("description", ""),
                "owner_name": owner_name,
                "owner_id": owner.get("id"),
                "is_public": playlist_info.get("public", False),
                "collaborative": playlist_info.get("collaborative", False),
                "follower_count": playlist_info.get("followers", {}).get("total"),
            }

        except spotipy.SpotifyException as e:
            logger.error(f"Spotify API error fetching playlist details: {e}")
            raise


def convert_spotify_track_to_connector(spotify_track: dict[str, Any]) -> ConnectorTrack:
    """Convert Spotify track data to ConnectorTrack domain model."""
    artists = [Artist(name=artist["name"]) for artist in spotify_track["artists"]]

    # Parse release date based on precision
    release_date = None
    if "album" in spotify_track and "release_date" in spotify_track["album"]:
        date_str = spotify_track["album"]["release_date"]
        precision = spotify_track["album"].get("release_date_precision", "day")

        try:
            if precision == "year":
                release_date = datetime.strptime(date_str, "%Y").replace(tzinfo=UTC)
            elif precision == "month":
                release_date = datetime.strptime(date_str, "%Y-%m").replace(tzinfo=UTC)
            else:  # day precision
                release_date = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    tzinfo=UTC,
                )
        except ValueError as e:
            logger.warning(f"Failed to parse release date '{date_str}': {e}")

    # Prepare raw metadata with essential information
    raw_metadata = {
        "popularity": spotify_track.get("popularity", 0),
        "album_id": spotify_track["album"].get("id"),
        "explicit": spotify_track.get("explicit", False),
    }

    return ConnectorTrack(
        connector_name="spotify",
        connector_track_id=spotify_track["id"],
        title=spotify_track["name"],
        artists=artists,
        album=spotify_track["album"]["name"],
        duration_ms=spotify_track["duration_ms"],
        release_date=release_date,
        isrc=spotify_track.get("external_ids", {}).get("isrc"),
        raw_metadata=raw_metadata,
        last_updated=datetime.now(UTC),
    )


# Removed convert_spotify_track_to_domain in favor of repository layer mapping


def convert_spotify_playlist_to_connector(
    spotify_playlist: dict[str, Any],
) -> ConnectorPlaylist:
    """Convert Spotify playlist data to ConnectorPlaylist domain model.

    Args:
        spotify_playlist: Raw playlist data from Spotify API

    Returns:
        ConnectorPlaylist model representing the external service playlist
    """
    # Extract owner information
    owner = spotify_playlist.get("owner", {}).get(
        "display_name"
    ) or spotify_playlist.get("owner", {}).get("id")

    owner_id = spotify_playlist.get("owner", {}).get("id")
    collaborative = spotify_playlist.get("collaborative", False)

    # Prepare raw metadata with essential information
    raw_metadata = {
        "snapshot_id": spotify_playlist.get("snapshot_id"),
        "tracks_href": spotify_playlist.get("tracks", {}).get("href"),
        "images": spotify_playlist.get("images", []),
        "total_tracks": spotify_playlist.get("tracks", {}).get("total", 0),
    }

    return ConnectorPlaylist(
        connector_name="spotify",
        connector_playlist_id=spotify_playlist["id"],
        name=spotify_playlist["name"],
        description=spotify_playlist.get("description"),
        owner=owner,
        owner_id=owner_id,
        is_public=spotify_playlist.get("public", False),
        collaborative=collaborative,
        follower_count=spotify_playlist.get("followers", {}).get("total"),
        raw_metadata=raw_metadata,
        last_updated=datetime.now(UTC),
    )


def get_connector_config() -> ConnectorConfig:
    """Spotify connector configuration."""
    return {
        "extractors": {
            "popularity": lambda obj: extract_metric(
                obj,
                ["popularity", "spotify_popularity"],
            ),
            "spotify_popularity": lambda obj: extract_metric(
                obj,
                ["popularity", "spotify_popularity"],
            ),
            "explicit_flag": lambda obj: extract_metric(
                obj,
                ["explicit", "explicit_flag"],
            ),
        },
        "dependencies": ["auth"],
        "factory": lambda _params: SpotifyConnector(),
        "metrics": SpotifyMetricResolver.FIELD_MAP,
    }


@define(frozen=True, slots=True)
class SpotifyMetricResolver(BaseMetricResolver):
    """Resolves Spotify metrics from persistence layer."""

    # Map metric names to connector metadata fields
    FIELD_MAP: ClassVar[dict[str, str]] = {
        "spotify_popularity": "popularity",
        "explicit_flag": "explicit",
    }

    # Connector name for database operations
    CONNECTOR: ClassVar[str] = "spotify"


# Register all metric resolvers at once
register_metrics(SpotifyMetricResolver(), SpotifyMetricResolver.FIELD_MAP)

# Register track converter for service registry
register_track_converter("spotify", convert_spotify_track_to_connector)
