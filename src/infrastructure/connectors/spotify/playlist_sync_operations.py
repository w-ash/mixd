"""Spotify differential playlist synchronization operations.

Handles minimal playlist updates (add/remove/move) with canonical URI resolution.
"""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from uuid import UUID

from attrs import define, evolve, field

from src.config import get_logger, settings
from src.config.constants import BusinessLimits
from src.domain.playlist import PlaylistOperation, PlaylistOperationType
from src.domain.repositories.interfaces import TrackRepositoryProtocol
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

logger = get_logger(__name__).bind(service="spotify_playlist_sync")

type _OperationExecutor = Callable[
    [str, list[PlaylistOperation], str | None], Awaitable[str | None]
]


@define(slots=True)
class SpotifyPlaylistSyncOperations:
    """Differential playlist sync: URI resolution → remove → move → add."""

    client: SpotifyAPIClient = field()

    async def execute_playlist_operations(
        self,
        playlist_id: str,
        operations: list[PlaylistOperation],
        snapshot_id: str | None = None,
        track_repo: TrackRepositoryProtocol | None = None,
    ) -> str | None:
        """Execute differential playlist operations with URI translation."""
        if not operations:
            return snapshot_id

        logger.info(
            "Executing playlist operations",
            playlist_id=playlist_id,
            total=len(operations),
        )

        # Resolve canonical URIs to Spotify URIs
        if track_repo:
            operations = await self._resolve_canonical_uris_to_spotify(
                operations, track_repo
            )
            logger.info(f"Resolved {len(operations)} operations after URI translation")

        # Group by type
        ops_by_type: dict[PlaylistOperationType, list[PlaylistOperation]] = {
            PlaylistOperationType.REMOVE: [],
            PlaylistOperationType.MOVE: [],
            PlaylistOperationType.ADD: [],
        }
        for op in operations:
            ops_by_type[op.operation_type].append(op)

        logger.info(
            "Operations breakdown",
            removes=len(ops_by_type[PlaylistOperationType.REMOVE]),
            moves=len(ops_by_type[PlaylistOperationType.MOVE]),
            adds=len(ops_by_type[PlaylistOperationType.ADD]),
        )

        # Execute in optimal order: remove → move → add
        current_snapshot = snapshot_id
        try:
            current_snapshot = await self._execute_operation_group(
                "remove",
                playlist_id,
                ops_by_type[PlaylistOperationType.REMOVE],
                current_snapshot,
                self._execute_remove_operations,
            )
            current_snapshot = await self._execute_operation_group(
                "move",
                playlist_id,
                ops_by_type[PlaylistOperationType.MOVE],
                current_snapshot,
                self._execute_move_operations,
            )
            current_snapshot = await self._execute_operation_group(
                "add",
                playlist_id,
                ops_by_type[PlaylistOperationType.ADD],
                current_snapshot,
                self._execute_add_operations,
            )

            logger.info("All operations completed", final_snapshot=current_snapshot)

        except Exception as e:
            logger.error(
                "Playlist operations failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise
        else:
            return current_snapshot

    async def _execute_operation_group(
        self,
        operation_name: str,
        playlist_id: str,
        operations: list[PlaylistOperation],
        snapshot_id: str | None,
        executor: _OperationExecutor,
    ) -> str | None:
        """Execute a group of operations with consistent error handling."""
        if not operations:
            return snapshot_id

        logger.info(f"Executing {len(operations)} {operation_name} operations")
        try:
            new_snapshot = await executor(playlist_id, operations, snapshot_id)
            logger.info(f"{operation_name.capitalize()} operations completed")
        except Exception as e:
            logger.error(
                f"{operation_name.capitalize()} operations failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise
        else:
            return new_snapshot

    async def _resolve_canonical_uris_to_spotify(
        self, operations: list[PlaylistOperation], track_repo: TrackRepositoryProtocol
    ) -> list[PlaylistOperation]:
        """Convert canonical URIs to Spotify URIs via database lookup."""
        # Collect canonical track IDs
        canonical_ids: set[UUID] = set()
        for op in operations:
            if op.spotify_uri and op.spotify_uri.startswith("canonical:"):
                try:
                    track_id = UUID(op.spotify_uri.split(":", 1)[1])
                    canonical_ids.add(track_id)
                except ValueError, IndexError:
                    logger.warning("Invalid canonical URI", uri=op.spotify_uri)

        if not canonical_ids:
            # All operations already have Spotify URIs
            return [op for op in operations if op.spotify_uri]

        logger.info(
            f"Loading {len(canonical_ids)} tracks for URI resolution",
            sample_ids=sorted(canonical_ids)[
                : BusinessLimits.DEBUG_LOG_TRUNCATION_LIMIT
            ],
        )

        # Bulk load tracks
        track_map = await track_repo.find_tracks_by_ids(list(canonical_ids))
        logger.info(f"Found {len(track_map)}/{len(canonical_ids)} tracks in database")

        # Resolve operations
        resolved: list[PlaylistOperation] = []
        stats: dict[str, int] = {
            "resolved": 0,
            "missing_track": 0,
            "missing_spotify_id": 0,
        }

        for op in operations:
            if not op.spotify_uri:
                continue

            if op.spotify_uri.startswith("canonical:"):
                try:
                    track_id = UUID(op.spotify_uri.split(":", 1)[1])
                    track = track_map.get(track_id)

                    if not track:
                        stats["missing_track"] += 1
                        continue

                    spotify_id = track.connector_track_identifiers.get("spotify")
                    if not spotify_id:
                        stats["missing_spotify_id"] += 1
                        logger.warning(
                            "Track missing Spotify ID",
                            canonical_id=track_id,
                            title=track.title,
                        )
                        continue

                    resolved.append(
                        evolve(op, spotify_uri=f"spotify:track:{spotify_id}")
                    )
                    stats["resolved"] += 1

                except ValueError, IndexError:
                    continue

            elif op.spotify_uri.startswith("spotify:track:"):
                resolved.append(op)

        logger.info(
            "URI resolution complete",
            resolved=stats["resolved"],
            missing_track=stats["missing_track"],
            missing_spotify_id=stats["missing_spotify_id"],
            total_output=len(resolved),
        )

        return resolved

    def _validate_operations(
        self, operations: list[PlaylistOperation], operation_type: PlaylistOperationType
    ) -> list[tuple[int, PlaylistOperation]]:
        """Validate operations and return list of (index, operation) tuples."""
        valid: list[tuple[int, PlaylistOperation]] = []

        for i, op in enumerate(operations):
            # Common validation
            if not op.spotify_uri:
                continue
            if not op.spotify_uri.startswith("spotify:track:"):
                continue

            # Type-specific validation
            if operation_type == PlaylistOperationType.REMOVE:
                valid.append((i, op))

            elif operation_type == PlaylistOperationType.ADD:
                if op.position < 0:
                    continue
                valid.append((i, op))

            elif operation_type == PlaylistOperationType.MOVE:
                if op.old_position is None:
                    continue
                if op.old_position < 0 or op.position < 0:
                    continue
                valid.append((i, op))

        if len(valid) < len(operations):
            logger.info(
                f"Filtered {operation_type.value} operations",
                valid=len(valid),
                invalid=len(operations) - len(valid),
            )

        return valid

    async def _execute_remove_operations(
        self,
        playlist_id: str,
        remove_ops: list[PlaylistOperation],
        snapshot_id: str | None,
    ) -> str | None:
        """Execute remove operations batched by track URI."""
        valid_ops = self._validate_operations(remove_ops, PlaylistOperationType.REMOVE)
        if not valid_ops:
            return snapshot_id

        # Group by URI with positions using defaultdict
        tracks_to_remove: defaultdict[str | None, list[int]] = defaultdict(list)
        for _, op in valid_ops:
            if op.old_position is not None:
                tracks_to_remove[op.spotify_uri].append(op.old_position)

        # Build items list
        items = [
            {"uri": uri, "positions": positions} if positions else {"uri": uri}
            for uri, positions in tracks_to_remove.items()
        ]

        # Batch execution
        current_snapshot = snapshot_id
        total_batches = (len(items) + 99) // 100
        failed_batches = 0

        for i in range(0, len(items), 100):
            batch = items[i : i + 100]
            try:
                result = (
                    await self.client.playlist_remove_specific_occurrences_of_items(
                        playlist_id=playlist_id,
                        items=batch,
                        snapshot_id=current_snapshot,
                    )
                )
                if result:
                    current_snapshot = result.snapshot_id
                else:
                    failed_batches += 1

                await asyncio.sleep(settings.api.spotify.request_delay)

            except Exception as e:
                failed_batches += 1
                logger.error(
                    f"Remove batch {i // 100 + 1}/{total_batches} failed",
                    error=str(e),
                )

        if failed_batches == total_batches:
            raise RuntimeError(
                f"All {total_batches} remove batches failed for playlist {playlist_id}"
            )

        return current_snapshot

    async def _execute_add_operations(
        self,
        playlist_id: str,
        add_ops: list[PlaylistOperation],
        snapshot_id: str | None,
    ) -> str | None:
        """Execute add operations individually with position tracking."""
        valid_ops = self._validate_operations(add_ops, PlaylistOperationType.ADD)
        if not valid_ops:
            return snapshot_id

        successful = 0
        failed = 0

        for _, op in valid_ops:
            if op.spotify_uri is None:
                raise RuntimeError(
                    "BUG: add op passed validation with None spotify_uri"
                )
            try:
                _ = await self.client.playlist_add_items(
                    playlist_id=playlist_id,
                    items=[op.spotify_uri],
                    position=op.position,
                )
                successful += 1
                await asyncio.sleep(settings.api.spotify.request_delay)

            except Exception as e:
                failed += 1
                logger.error(f"Add operation failed: {e}")

        # Fetch updated snapshot after successful adds
        if successful > 0:
            snapshot_id = await self._get_updated_snapshot(playlist_id, snapshot_id)

        logger.info(f"Add operations: {successful} succeeded, {failed} failed")

        if failed > 0 and successful == 0:
            raise RuntimeError(
                f"All {len(valid_ops)} add operations failed for playlist {playlist_id}"
            )

        return snapshot_id

    async def _execute_move_operations(
        self,
        playlist_id: str,
        move_ops: list[PlaylistOperation],
        snapshot_id: str | None,
    ) -> str | None:
        """Execute move operations individually with bounds checking."""
        # Get current playlist size for validation
        try:
            playlist_info = await self.client.get_playlist(playlist_id)
            current_track_count = playlist_info.items.total if playlist_info else None
        except Exception as e:
            logger.warning(f"Could not fetch playlist size: {e}")
            current_track_count = None

        # Validate operations
        valid_ops: list[tuple[int, PlaylistOperation]] = []
        for i, op in enumerate(move_ops):
            if op.old_position is None:
                continue
            if op.old_position < 0 or op.position < 0:
                continue

            # Bounds checking
            if current_track_count is not None:
                if op.old_position >= current_track_count:
                    continue
                if op.position > current_track_count:
                    continue

            valid_ops.append((i, op))

        if not valid_ops:
            return snapshot_id

        if len(valid_ops) < len(move_ops):
            logger.info(
                "Filtered move operations",
                valid=len(valid_ops),
                invalid=len(move_ops) - len(valid_ops),
            )

        successful = 0
        failed = 0
        current_snapshot = snapshot_id

        for _, op in valid_ops:
            if op.old_position is None:
                raise RuntimeError(
                    "BUG: move op passed validation with None old_position"
                )
            try:
                result = await self.client.playlist_reorder_items(
                    playlist_id=playlist_id,
                    range_start=op.old_position,
                    insert_before=op.position,
                    range_length=1,
                    snapshot_id=current_snapshot,
                )

                if result:
                    current_snapshot = result.snapshot_id
                    successful += 1
                else:
                    failed += 1

                await asyncio.sleep(settings.api.spotify.request_delay)

            except Exception as e:
                failed += 1
                logger.error(f"Move operation failed: {e}")

        logger.info(f"Move operations: {successful} succeeded, {failed} failed")

        if failed > 0 and successful == 0:
            raise RuntimeError(
                f"All {len(valid_ops)} move operations failed for playlist {playlist_id}"
            )

        return current_snapshot

    async def _get_updated_snapshot(
        self, playlist_id: str, fallback: str | None
    ) -> str | None:
        """Fetch updated playlist snapshot ID."""
        try:
            playlist_info = await self.client.get_playlist(playlist_id)
        except Exception as e:
            logger.warning(f"Could not fetch updated snapshot: {e}")
            return fallback
        else:
            return playlist_info.snapshot_id or fallback if playlist_info else fallback
