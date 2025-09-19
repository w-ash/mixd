"""Spotify business operations - Complex workflows and orchestration.

This module handles complex business logic for Spotify operations that require
multiple API calls, batch processing, or sophisticated coordination. It uses
the SpotifyAPIClient for individual API calls and integrates with shared
services for optimization.

Key components:
- SpotifyOperations: High-level business workflows
- Playlist creation and management with batch processing
- Bulk track operations with intelligent batching
- Integration with PlaylistOperationService for optimization
- Complex multi-step operations requiring coordination

The operations layer sits between the thin API client and the connector facade,
providing reusable business logic while maintaining clean separation of concerns.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any, NoReturn

from attrs import define, field

from src.config import get_logger, settings
from src.domain.entities import (
    ConnectorPlaylist,
    ConnectorPlaylistItem,
    ConnectorTrack,
    Playlist,
    Track,
)
from src.domain.playlist import PlaylistOperationType
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient
from src.infrastructure.connectors.spotify.conversions import (
    convert_spotify_playlist_to_connector,
    convert_spotify_track_to_connector,
    extract_spotify_track_uris,
    extract_track_metadata_for_playlist_item,
    parse_spotify_timestamp,
    validate_non_empty,
)

# Get contextual logger for operations
logger = get_logger(__name__).bind(service="spotify_operations")


@define(slots=True)
class SpotifyOperations:
    """Business logic service for complex Spotify operations.

    Handles multi-step workflows, batch processing, and coordination of
    multiple API calls. Uses SpotifyAPIClient for individual API interactions.

    Example:
        >>> client = SpotifyAPIClient()
        >>> operations = SpotifyOperations(client)
        >>> playlist_id = await operations.create_playlist_with_tracks(
        ...     "My Playlist", tracks
        ... )
    """

    client: SpotifyAPIClient = field()

    # Bulk Track Operations

    async def get_tracks_by_ids(
        self, track_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Fetch multiple tracks from Spotify with simple bulk batching."""
        if early_return := validate_non_empty(track_ids, {}):
            return early_return

        results = {}

        # Process in batches using Spotify's bulk API (50 tracks per call)
        batch_size = settings.api.spotify_batch_size
        total_batches = (len(track_ids) + batch_size - 1) // batch_size

        logger.info(f"Fetching {len(track_ids)} tracks in {total_batches} batches")

        for i in range(0, len(track_ids), batch_size):
            batch_ids = track_ids[i : i + batch_size]

            try:
                # Use bulk tracks API - single call for up to 50 tracks
                tracks_response = await self.client.get_tracks_bulk(batch_ids)

                if tracks_response and "tracks" in tracks_response:
                    for track in tracks_response["tracks"]:
                        if track and "id" in track:
                            current_id = track["id"]
                            results[current_id] = track

                            # Handle Spotify relinking: if track has linked_from,
                            # also map the original track ID to this data
                            linked_from = track.get("linked_from")
                            if linked_from and "id" in linked_from:
                                original_id = linked_from["id"]
                                results[original_id] = track
                                logger.debug(
                                    f"Relinked track found: {original_id} -> {current_id}"
                                )
                        else:
                            logger.warning("Received null track in batch response")

            except Exception as e:
                logger.error(
                    f"Failed to fetch batch {i // batch_size + 1}/{total_batches}: {e}"
                )
                continue

            # Brief delay between requests if configured
            if settings.api.spotify_request_delay > 0:
                await asyncio.sleep(settings.api.spotify_request_delay)

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

        # Map back to track.id format expected by enricher
        return {
            track.id: spotify_data[spotify_id]
            for track, spotify_id in spotify_mapped
            if spotify_id is not None
            and track.id is not None
            and spotify_id in spotify_data
        }

    # Advanced Playlist Operations

    async def get_playlist_with_all_tracks(self, playlist_id: str) -> ConnectorPlaylist:
        """Fetch a Spotify playlist with all tracks using pagination."""
        # Get initial playlist data
        raw_playlist = await self.client.get_playlist(playlist_id)
        if not isinstance(raw_playlist, dict):
            raise TypeError(f"Invalid playlist response for ID {playlist_id}")

        # Handle pagination to get all tracks
        tracks = raw_playlist["tracks"]
        all_items = tracks["items"]

        # Paginate until we get all tracks
        while tracks and tracks.get("next"):
            tracks = await self.client.get_next_page(tracks)
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
                added_at = item.get("added_at")

                # Create ConnectorPlaylistItem with track ID and metadata
                playlist_item = ConnectorPlaylistItem(
                    connector_track_identifier=track["id"],
                    position=idx,
                    added_at=added_at,
                    added_by_id=item.get("added_by", {}).get("id"),
                    extras={
                        "is_local": item.get("is_local", False),
                        **extract_track_metadata_for_playlist_item(track),
                        "added_at": added_at,  # Store in extras for easy access
                        "full_track_data": track,  # Store complete track data to avoid additional API calls
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

        def _raise_playlist_creation_error() -> NoReturn:
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

            playlist_id = playlist["id"]

            # Add tracks in batches if any
            if spotify_track_uris:
                await self._add_tracks_to_playlist_batched(
                    playlist_id, spotify_track_uris
                )

            return playlist_id

        except Exception as e:
            logger.error(f"Error creating playlist '{name}': {e}")
            raise

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
            f"with {len(spotify_track_uris)} tracks"
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
            await self.client.playlist_replace_items(playlist_id, first_batch)

            # Add remaining tracks in batches
            remaining_tracks = track_uris[large_batch_size:]
            if remaining_tracks:
                await self._add_tracks_to_playlist_batched(
                    playlist_id, remaining_tracks
                )
        else:
            # Clear playlist if no tracks
            await self.client.playlist_replace_items(playlist_id, [])

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
                await self.client.playlist_add_items(
                    playlist_id=playlist_id, items=batch_uris
                )
                logger.debug(f"Added batch {i // large_batch_size + 1}/{total_batches}")

            except Exception as e:
                logger.error(
                    f"Failed to add batch {i // large_batch_size + 1}/{total_batches}: {e}"
                )
                continue

            # Brief delay between requests if configured
            if settings.api.spotify_request_delay > 0:
                await asyncio.sleep(settings.api.spotify_request_delay)

    # Differential Playlist Operations

    async def execute_playlist_operations(
        self,
        playlist_id: str,
        operations: list,
        snapshot_id: str | None = None,
        track_repo=None,
    ) -> str | None:
        """Execute a list of differential playlist operations with URI translation."""
        if not operations:
            return snapshot_id

        logger.info(
            "Starting playlist operations execution",
            playlist_id=playlist_id,
            total_operations=len(operations),
            snapshot_id=snapshot_id,
        )

        # Resolve canonical URIs to Spotify URIs if track_repo is available
        original_operations_count = len(operations)
        if track_repo:
            try:
                operations = await self._resolve_canonical_uris_to_spotify(
                    operations, track_repo
                )
                logger.info(
                    "URI resolution completed",
                    original_count=original_operations_count,
                    resolved_count=len(operations),
                    filtered_out=original_operations_count - len(operations),
                )
            except Exception as e:
                logger.error(
                    "Failed to resolve canonical URIs to Spotify URIs",
                    error=str(e),
                    error_type=type(e).__name__,
                    original_operations=original_operations_count,
                )
                raise ValueError(f"URI resolution failed: {e}") from e

        current_snapshot = snapshot_id

        # Group operations by type for cleaner execution
        remove_ops = [
            op for op in operations if op.operation_type == PlaylistOperationType.REMOVE
        ]
        move_ops = [
            op for op in operations if op.operation_type == PlaylistOperationType.MOVE
        ]
        add_ops = [
            op for op in operations if op.operation_type == PlaylistOperationType.ADD
        ]

        logger.info(
            "Operations breakdown",
            removes=len(remove_ops),
            moves=len(move_ops),
            adds=len(add_ops),
            total_grouped=len(remove_ops) + len(move_ops) + len(add_ops),
        )

        # Track partial success for better error handling
        execution_results = {
            "removes_attempted": len(remove_ops),
            "removes_completed": 0,
            "moves_attempted": len(move_ops),
            "moves_completed": 0,
            "adds_attempted": len(add_ops),
            "adds_completed": 0,
            "snapshot_id": current_snapshot,
            "errors": [],
        }

        try:
            # Execute in optimal order: remove → move → add
            if remove_ops:
                logger.info(f"Executing {len(remove_ops)} remove operations")
                try:
                    current_snapshot = await self._execute_remove_operations(
                        playlist_id, remove_ops, current_snapshot
                    )
                    execution_results["removes_completed"] = len(remove_ops)
                    execution_results["snapshot_id"] = current_snapshot
                    logger.info(
                        "Remove operations completed successfully",
                        new_snapshot=current_snapshot,
                    )
                except Exception as e:
                    error_msg = f"Remove operations failed: {e}"
                    execution_results["errors"].append(error_msg)
                    logger.error(
                        error_msg,
                        error_type=type(e).__name__,
                        remove_count=len(remove_ops),
                        playlist_id=playlist_id,
                    )
                    raise

            if move_ops:
                logger.info(f"Executing {len(move_ops)} move operations")
                try:
                    current_snapshot = await self._execute_move_operations(
                        playlist_id, move_ops, current_snapshot
                    )
                    execution_results["moves_completed"] = len(move_ops)
                    execution_results["snapshot_id"] = current_snapshot
                    logger.info(
                        "Move operations completed successfully",
                        new_snapshot=current_snapshot,
                    )
                except Exception as e:
                    error_msg = f"Move operations failed: {e}"
                    execution_results["errors"].append(error_msg)
                    logger.error(
                        error_msg,
                        error_type=type(e).__name__,
                        move_count=len(move_ops),
                        playlist_id=playlist_id,
                    )
                    raise

            if add_ops:
                logger.info(f"Executing {len(add_ops)} add operations")
                try:
                    current_snapshot = await self._execute_add_operations(
                        playlist_id, add_ops, current_snapshot
                    )
                    execution_results["adds_completed"] = len(add_ops)
                    execution_results["snapshot_id"] = current_snapshot
                    logger.info(
                        "Add operations completed successfully",
                        new_snapshot=current_snapshot,
                    )
                except Exception as e:
                    error_msg = f"Add operations failed: {e}"
                    execution_results["errors"].append(error_msg)
                    logger.error(
                        error_msg,
                        error_type=type(e).__name__,
                        add_count=len(add_ops),
                        playlist_id=playlist_id,
                    )
                    raise

            logger.info(
                "All playlist operations completed successfully",
                final_snapshot=current_snapshot,
                execution_summary=execution_results,
            )
            return current_snapshot

        except Exception as e:
            logger.error(
                "Playlist operations execution failed",
                playlist_id=playlist_id,
                error=str(e),
                error_type=type(e).__name__,
                execution_results=execution_results,
                partial_success=any([
                    execution_results["removes_completed"] > 0,
                    execution_results["moves_completed"] > 0,
                    execution_results["adds_completed"] > 0,
                ]),
            )
            raise

    async def _resolve_canonical_uris_to_spotify(
        self, operations: list, track_repo
    ) -> list:
        """Convert canonical URIs in operations to Spotify URIs with detailed logging.

        Args:
            operations: List of playlist operations with canonical URIs
            track_repo: Track repository for looking up canonical tracks

        Returns:
            List of operations with Spotify URIs, filtered to valid ones only
        """
        logger.info(
            "Starting canonical URI resolution",
            total_operations=len(operations),
        )

        resolved_operations = []
        canonical_track_ids = set()

        # Track resolution statistics
        stats = {
            "canonical_uris_found": 0,
            "invalid_canonical_format": 0,
            "already_spotify_uris": 0,
            "unknown_uri_format": 0,
            "missing_uri": 0,
            "database_lookups_needed": 0,
            "tracks_found_in_db": 0,
            "tracks_missing_from_db": 0,
            "tracks_missing_spotify_id": 0,
            "successful_resolutions": 0,
            "evolution_failures": 0,
        }

        # Phase 1: Collect all canonical track IDs from operations
        logger.debug("Phase 1: Collecting canonical track IDs from operations")
        for i, op in enumerate(operations):
            if not op.spotify_uri:
                stats["missing_uri"] += 1
                logger.debug(
                    "Operation missing spotify_uri",
                    operation_index=i,
                    operation_type=op.operation_type.value if hasattr(op.operation_type, 'value') else str(op.operation_type),
                )
                continue

            if op.spotify_uri.startswith("canonical:"):
                stats["canonical_uris_found"] += 1
                try:
                    track_id = int(op.spotify_uri.split(":", 1)[1])
                    canonical_track_ids.add(track_id)
                except (ValueError, IndexError) as e:
                    stats["invalid_canonical_format"] += 1
                    logger.warning(
                        "Invalid canonical URI format",
                        operation_index=i,
                        uri=op.spotify_uri,
                        error=str(e),
                    )
                    continue
            elif op.spotify_uri.startswith("spotify:track:"):
                stats["already_spotify_uris"] += 1
            else:
                stats["unknown_uri_format"] += 1
                logger.warning(
                    "Unknown URI format detected",
                    operation_index=i,
                    uri=op.spotify_uri,
                )

        stats["database_lookups_needed"] = len(canonical_track_ids)

        logger.debug(
            "Phase 1 complete - URI analysis",
            canonical_track_ids=sorted(canonical_track_ids),
            **{k: v for k, v in stats.items() if v > 0}
        )

        # Phase 2: Bulk load tracks by IDs
        track_map = {}
        if canonical_track_ids:
            canonical_ids_list = list(canonical_track_ids)
            logger.info(
                "Phase 2: Loading tracks from database",
                track_count=len(canonical_ids_list),
                track_ids_sample=sorted(canonical_ids_list)[:10],  # Show first 10 for debugging
                all_track_ids=sorted(canonical_ids_list) if len(canonical_ids_list) <= 20 else None,
                track_repo_type=type(track_repo).__name__
            )

            try:
                logger.debug(
                    "Calling find_tracks_by_ids",
                    method_name="find_tracks_by_ids",
                    input_type=type(canonical_ids_list).__name__,
                    input_length=len(canonical_ids_list)
                )
                track_map = await track_repo.find_tracks_by_ids(canonical_ids_list)
                logger.debug(
                    "Database call completed successfully",
                    returned_type=type(track_map).__name__,
                    returned_length=len(track_map) if track_map else 0
                )

                stats["tracks_found_in_db"] = len(track_map)
                stats["tracks_missing_from_db"] = len(canonical_track_ids) - len(track_map)

                missing_track_ids = canonical_track_ids - set(track_map.keys())
                logger.info(
                    "Phase 2 complete - Database loading results",
                    requested_count=len(canonical_track_ids),
                    found_count=stats["tracks_found_in_db"],
                    missing_count=stats["tracks_missing_from_db"],
                    success_rate=f"{(stats['tracks_found_in_db'] / len(canonical_track_ids) * 100):.1f}%",
                    found_track_ids=sorted(track_map.keys())[:10] if track_map else [],
                    missing_track_ids=sorted(missing_track_ids)[:10] if missing_track_ids else [],
                    all_requested_ids=sorted(canonical_track_ids) if len(canonical_track_ids) <= 10 else None,
                )

                # Log connector identifier availability for found tracks
                for track_id, track in track_map.items():
                    available_connectors = list(track.connector_track_identifiers.keys())
                    has_spotify = "spotify" in track.connector_track_identifiers
                    spotify_id = track.connector_track_identifiers.get("spotify")

                    logger.debug(
                        "Track connector data",
                        track_id=track_id,
                        track_title=track.title,
                        available_connectors=available_connectors,
                        has_spotify_id=has_spotify,
                        spotify_id=spotify_id if has_spotify else None,
                    )

                    if not has_spotify:
                        stats["tracks_missing_spotify_id"] += 1

            except Exception as e:
                import traceback
                logger.error(
                    "Database track loading failed",
                    track_count=len(canonical_track_ids),
                    track_ids_sample=sorted(canonical_track_ids)[:10],
                    all_track_ids=sorted(canonical_track_ids) if len(canonical_track_ids) <= 20 else None,
                    error_message=str(e),
                    error_type=type(e).__name__,
                    error_module=getattr(e, '__module__', 'unknown'),
                    track_repo_type=type(track_repo).__name__,
                    track_repo_session_active=hasattr(track_repo, 'session') and track_repo.session is not None,
                    full_traceback=traceback.format_exc(),
                )
                raise

        # Phase 3: Resolve operations
        logger.debug("Phase 3: Resolving individual operations")
        for i, op in enumerate(operations):
            if not op.spotify_uri:
                logger.debug(f"Skipping operation {i}: missing spotify_uri")
                continue

            if op.spotify_uri.startswith("canonical:"):
                # Resolve canonical URI to Spotify URI
                try:
                    track_id = int(op.spotify_uri.split(":", 1)[1])
                    track = track_map.get(track_id)

                    if not track:
                        logger.warning(
                            "Track not found in database",
                            operation_index=i,
                            canonical_id=track_id,
                            canonical_uri=op.spotify_uri,
                            operation_type=op.operation_type.value if hasattr(op.operation_type, 'value') else str(op.operation_type),
                        )
                        continue

                    spotify_id = track.connector_track_identifiers.get("spotify")
                    if not spotify_id:
                        available_connectors = list(track.connector_track_identifiers.keys())
                        logger.warning(
                            "Track found but missing Spotify connector ID",
                            operation_index=i,
                            canonical_id=track_id,
                            track_title=track.title,
                            available_connectors=available_connectors,
                            operation_type=op.operation_type.value if hasattr(op.operation_type, 'value') else str(op.operation_type),
                        )
                        continue

                    # Update operation with Spotify URI
                    try:
                        from attrs import evolve
                        resolved_op = evolve(op, spotify_uri=f"spotify:track:{spotify_id}")
                        resolved_operations.append(resolved_op)
                        stats["successful_resolutions"] += 1

                        logger.debug(
                            "Successfully resolved canonical URI",
                            operation_index=i,
                            canonical_id=track_id,
                            spotify_id=spotify_id,
                            spotify_uri=f"spotify:track:{spotify_id}",
                            track_title=track.title,
                        )
                    except Exception as e:
                        stats["evolution_failures"] += 1
                        logger.error(
                            "Failed to evolve operation with Spotify URI",
                            operation_index=i,
                            canonical_id=track_id,
                            spotify_id=spotify_id,
                            error=str(e),
                            error_type=type(e).__name__,
                        )
                        continue

                except (ValueError, IndexError) as e:
                    logger.warning(
                        "Invalid canonical URI format during resolution",
                        operation_index=i,
                        uri=op.spotify_uri,
                        error=str(e),
                    )
                    continue

            elif op.spotify_uri.startswith("spotify:track:"):
                # Already a Spotify URI - keep as is
                resolved_operations.append(op)
                logger.debug(
                    "Operation already has Spotify URI",
                    operation_index=i,
                    spotify_uri=op.spotify_uri,
                )

            else:
                logger.warning(
                    "Unknown URI format, skipping operation",
                    operation_index=i,
                    uri=op.spotify_uri,
                    operation_type=op.operation_type.value if hasattr(op.operation_type, 'value') else str(op.operation_type),
                )
                continue

        # Final summary
        logger.info(
            "Canonical URI resolution completed",
            input_operations=len(operations),
            output_operations=len(resolved_operations),
            resolution_stats=stats,
            success_rate=f"{(stats['successful_resolutions'] / max(stats['canonical_uris_found'], 1)) * 100:.1f}%" if stats['canonical_uris_found'] > 0 else "N/A",
        )

        if stats["tracks_missing_spotify_id"] > 0:
            logger.warning(
                f"{stats['tracks_missing_spotify_id']} tracks found in database but missing Spotify connector IDs - these may need to be matched to Spotify first"
            )

        if stats["tracks_missing_from_db"] > 0:
            logger.warning(
                f"{stats['tracks_missing_from_db']} canonical track IDs not found in database - these tracks may not exist or may have been deleted"
            )

        return resolved_operations

    async def _execute_remove_operations(
        self,
        playlist_id: str,
        remove_ops: list,
        snapshot_id: str | None,
    ) -> str | None:
        """Execute remove operations, batched by track URI with detailed error tracking."""
        if not remove_ops:
            return snapshot_id

        logger.debug(
            "Starting remove operations",
            remove_count=len(remove_ops),
            playlist_id=playlist_id,
        )

        # Pre-validate operations
        valid_ops = []
        for op in remove_ops:
            if not op.spotify_uri:
                logger.warning(
                    "Remove operation missing spotify_uri, skipping",
                    operation=str(op),
                )
                continue
            if not op.spotify_uri.startswith("spotify:track:"):
                logger.warning(
                    "Remove operation has invalid Spotify URI format, skipping",
                    uri=op.spotify_uri,
                    operation=str(op),
                )
                continue
            valid_ops.append(op)

        if not valid_ops:
            logger.warning("No valid remove operations after validation")
            return snapshot_id

        logger.debug(
            "Remove operations validation completed",
            valid_operations=len(valid_ops),
            invalid_operations=len(remove_ops) - len(valid_ops),
        )

        # Group removes by track URI to optimize API calls
        tracks_to_remove = {}
        for op in valid_ops:
            if op.spotify_uri not in tracks_to_remove:
                tracks_to_remove[op.spotify_uri] = []
            if op.old_position is not None:
                tracks_to_remove[op.spotify_uri].append(op.old_position)

        # Execute removes in batches
        items_to_remove = []
        for uri, positions in tracks_to_remove.items():
            if positions:
                items_to_remove.append({"uri": uri, "positions": positions})
            else:
                items_to_remove.append({"uri": uri})

        if not items_to_remove:
            logger.debug("No items to remove after grouping")
            return snapshot_id

        # Track batch results
        successful_batches = 0
        failed_batches = 0
        total_batches = (len(items_to_remove) + 99) // 100

        logger.debug(
            "Executing remove operations in batches",
            total_items=len(items_to_remove),
            total_batches=total_batches,
        )

        current_snapshot = snapshot_id

        # Process in batches of 100
        for i in range(0, len(items_to_remove), 100):
            batch_num = (i // 100) + 1
            batch = items_to_remove[i : i + 100]

            try:
                logger.debug(
                    "Executing remove batch",
                    batch_number=batch_num,
                    batch_size=len(batch),
                    items=[item["uri"] for item in batch],
                )

                result = (
                    await self.client.playlist_remove_specific_occurrences_of_items(
                        playlist_id=playlist_id,
                        items=batch,
                        snapshot_id=current_snapshot,
                    )
                )

                if result and result.get("snapshot_id"):
                    current_snapshot = result["snapshot_id"]
                    successful_batches += 1
                    logger.debug(
                        "Remove batch completed successfully",
                        batch_number=batch_num,
                        new_snapshot=current_snapshot,
                    )
                else:
                    failed_batches += 1
                    logger.error(
                        "Remove batch returned no snapshot_id",
                        batch_number=batch_num,
                        batch_items=batch,
                        result=result,
                    )

                await asyncio.sleep(settings.api.spotify_request_delay)

            except Exception as e:
                failed_batches += 1
                logger.error(
                    "Remove batch failed",
                    batch_number=batch_num,
                    batch_size=len(batch),
                    error=str(e),
                    error_type=type(e).__name__,
                    batch_items=[item["uri"] for item in batch],
                    playlist_id=playlist_id,
                )
                # Continue with remaining batches
                continue

        logger.info(
            "Remove operations completed",
            successful_batches=successful_batches,
            failed_batches=failed_batches,
            total_batches=total_batches,
            final_snapshot=current_snapshot,
        )

        if failed_batches > 0 and successful_batches == 0:
            raise RuntimeError(
                f"All {total_batches} remove batches failed for playlist {playlist_id}"
            )

        return current_snapshot

    async def _execute_add_operations(
        self,
        playlist_id: str,
        add_ops: list,
        snapshot_id: str | None,
    ) -> str | None:
        """Execute add operations individually with detailed error tracking."""
        if not add_ops:
            return snapshot_id

        logger.debug(
            "Starting add operations",
            add_count=len(add_ops),
            playlist_id=playlist_id,
        )

        # Pre-validate operations
        valid_ops = []
        for i, op in enumerate(add_ops):
            if not op.spotify_uri:
                logger.warning(
                    "Add operation missing spotify_uri, skipping",
                    operation_index=i,
                    operation=str(op),
                )
                continue
            if not op.spotify_uri.startswith("spotify:track:"):
                logger.warning(
                    "Add operation has invalid Spotify URI format, skipping",
                    operation_index=i,
                    uri=op.spotify_uri,
                    operation=str(op),
                )
                continue
            if op.position is not None and op.position < 0:
                logger.warning(
                    "Add operation has invalid position, skipping",
                    operation_index=i,
                    position=op.position,
                    operation=str(op),
                )
                continue
            valid_ops.append((i, op))

        if not valid_ops:
            logger.warning("No valid add operations after validation")
            return snapshot_id

        logger.debug(
            "Add operations validation completed",
            valid_operations=len(valid_ops),
            invalid_operations=len(add_ops) - len(valid_ops),
        )

        # Track individual operation results
        successful_operations = 0
        failed_operations = 0
        current_snapshot = snapshot_id

        # Execute individual add operations
        for op_index, op in valid_ops:
            try:
                logger.debug(
                    "Executing add operation",
                    operation_index=op_index,
                    spotify_uri=op.spotify_uri,
                    position=op.position,
                )

                await self.client.playlist_add_items(
                    playlist_id=playlist_id,
                    items=[op.spotify_uri],
                    position=op.position,
                )

                successful_operations += 1
                logger.debug(
                    "Add operation completed successfully",
                    operation_index=op_index,
                    spotify_uri=op.spotify_uri,
                    position=op.position,
                )

                await asyncio.sleep(settings.api.spotify_request_delay)

            except Exception as e:
                failed_operations += 1
                logger.error(
                    "Add operation failed",
                    operation_index=op_index,
                    spotify_uri=op.spotify_uri,
                    position=op.position,
                    error=str(e),
                    error_type=type(e).__name__,
                    playlist_id=playlist_id,
                )
                # Continue with remaining operations
                continue

        # Get updated snapshot ID after successful adds
        if successful_operations > 0:
            try:
                logger.debug("Fetching updated playlist snapshot after add operations")
                playlist_info = await self.client.get_playlist(playlist_id)
                if playlist_info and playlist_info.get("snapshot_id"):
                    current_snapshot = playlist_info["snapshot_id"]
                    logger.debug(
                        "Updated snapshot retrieved successfully",
                        new_snapshot=current_snapshot,
                    )
                else:
                    logger.warning(
                        "Failed to retrieve updated snapshot after add operations",
                        playlist_info=playlist_info,
                    )
            except Exception as e:
                logger.error(
                    "Failed to fetch updated playlist snapshot",
                    error=str(e),
                    error_type=type(e).__name__,
                    playlist_id=playlist_id,
                )

        logger.info(
            "Add operations completed",
            successful_operations=successful_operations,
            failed_operations=failed_operations,
            total_operations=len(valid_ops),
            final_snapshot=current_snapshot,
        )

        if failed_operations > 0 and successful_operations == 0:
            raise RuntimeError(
                f"All {len(valid_ops)} add operations failed for playlist {playlist_id}"
            )

        return current_snapshot

    async def _execute_move_operations(
        self,
        playlist_id: str,
        move_ops: list,
        snapshot_id: str | None,
    ) -> str | None:
        """Execute move operations individually with detailed error tracking."""
        if not move_ops:
            return snapshot_id

        logger.debug(
            "Starting move operations",
            move_count=len(move_ops),
            playlist_id=playlist_id,
        )

        # Get current playlist info for bounds checking
        try:
            playlist_info = await self.client.get_playlist(playlist_id)
            if not playlist_info:
                logger.error(
                    "Could not fetch playlist info for bounds checking, skipping move operations",
                    playlist_id=playlist_id,
                )
                return snapshot_id

            current_track_count = playlist_info.get("tracks", {}).get("total", 0)
            logger.debug(
                "Current playlist track count for bounds checking",
                track_count=current_track_count,
                playlist_id=playlist_id,
            )
        except Exception as e:
            logger.warning(
                "Failed to fetch playlist info for bounds checking, proceeding without validation",
                error=str(e),
                playlist_id=playlist_id,
            )
            current_track_count = None

        # Pre-validate operations
        valid_ops = []
        for i, op in enumerate(move_ops):
            if op.old_position is None:
                logger.warning(
                    "Move operation missing old_position, skipping",
                    operation_index=i,
                    operation=str(op),
                )
                continue
            if op.position is None:
                logger.warning(
                    "Move operation missing position, skipping",
                    operation_index=i,
                    operation=str(op),
                )
                continue
            if op.old_position < 0:
                logger.warning(
                    "Move operation has invalid old_position, skipping",
                    operation_index=i,
                    old_position=op.old_position,
                    operation=str(op),
                )
                continue
            if op.position < 0:
                logger.warning(
                    "Move operation has invalid position, skipping",
                    operation_index=i,
                    position=op.position,
                    operation=str(op),
                )
                continue

            # Add bounds checking against current playlist length
            if current_track_count is not None:
                if op.old_position >= current_track_count:
                    logger.warning(
                        "Move operation old_position out of bounds, skipping",
                        operation_index=i,
                        old_position=op.old_position,
                        current_track_count=current_track_count,
                        operation=str(op),
                    )
                    continue
                if op.position > current_track_count:
                    logger.warning(
                        "Move operation position out of bounds, skipping",
                        operation_index=i,
                        position=op.position,
                        current_track_count=current_track_count,
                        operation=str(op),
                    )
                    continue

            valid_ops.append((i, op))

        if not valid_ops:
            logger.warning("No valid move operations after validation")
            return snapshot_id

        logger.debug(
            "Move operations validation completed",
            valid_operations=len(valid_ops),
            invalid_operations=len(move_ops) - len(valid_ops),
        )

        # Track individual operation results
        successful_operations = 0
        failed_operations = 0
        current_snapshot = snapshot_id

        # Execute individual move operations
        for op_index, op in valid_ops:
            try:
                logger.debug(
                    "Executing move operation",
                    operation_index=op_index,
                    old_position=op.old_position,
                    new_position=op.position,
                    spotify_uri=getattr(op, "spotify_uri", None),
                )

                result = await self.client.playlist_reorder_items(
                    playlist_id=playlist_id,
                    range_start=op.old_position,
                    insert_before=op.position,
                    range_length=1,
                    snapshot_id=current_snapshot,
                )

                if result and result.get("snapshot_id"):
                    current_snapshot = result["snapshot_id"]
                    successful_operations += 1
                    logger.debug(
                        "Move operation completed successfully",
                        operation_index=op_index,
                        old_position=op.old_position,
                        new_position=op.position,
                        new_snapshot=current_snapshot,
                    )
                else:
                    failed_operations += 1
                    logger.error(
                        "Move operation returned no snapshot_id",
                        operation_index=op_index,
                        old_position=op.old_position,
                        new_position=op.position,
                        result=result,
                    )

                await asyncio.sleep(settings.api.spotify_request_delay)

            except Exception as e:
                failed_operations += 1
                logger.error(
                    "Move operation failed",
                    operation_index=op_index,
                    old_position=op.old_position,
                    new_position=op.position,
                    error=str(e),
                    error_type=type(e).__name__,
                    playlist_id=playlist_id,
                )
                # Continue with remaining operations
                continue

        logger.info(
            "Move operations completed",
            successful_operations=successful_operations,
            failed_operations=failed_operations,
            total_operations=len(valid_ops),
            final_snapshot=current_snapshot,
        )

        if failed_operations > 0 and successful_operations == 0:
            raise RuntimeError(
                f"All {len(valid_ops)} move operations failed for playlist {playlist_id}"
            )

        return current_snapshot

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

            connector_tracks = []
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

            return connector_tracks, next_cursor

        except Exception as e:
            logger.error(f"Error fetching liked tracks: {e}")
            raise

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

        def _raise_playlist_not_found_error(playlist_id: str) -> NoReturn:
            raise ValueError(f"Playlist {playlist_id} not found")

        logger.debug(f"Fetching Spotify playlist details for {playlist_id}")

        try:
            playlist_info = await self.client.get_playlist(playlist_id)

            if not playlist_info:
                _raise_playlist_not_found_error(playlist_id)

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

        except Exception as e:
            logger.error(f"Error fetching playlist details: {e}")
            raise

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
                "snapshot_id": playlist_info.get("snapshot_id")
                if playlist_info
                else None,
                "last_modified": datetime.now(UTC).isoformat(),
            }

        except Exception as e:
            logger.error(f"Error appending tracks to playlist: {e}")
            raise
