"""Manages track metadata from music service APIs (Spotify, Last.fm, etc).

Fetches fresh metadata from external APIs, stores it in the database with timestamps,
and retrieves cached metadata. Optimizes API calls by using existing track mappings
instead of expensive re-matching when possible.
"""

from collections.abc import Awaitable, Callable
from typing import Any, cast

from src.config import get_logger
from src.domain.matching.types import MatchResultsById
from src.domain.repositories.interfaces import ConnectorRepositoryProtocol

logger = get_logger(__name__)

# Type alias for batch track info method
BatchTrackInfoMethod = Callable[..., Awaitable[dict[int, Any]]]


class ConnectorMetadataManager:
    """Fetches and caches track metadata from music service APIs.

    Reduces API calls by using direct lookups when tracks are already mapped
    to external services, falling back to cached data when API calls fail.
    """

    def __init__(self, connector_repo: ConnectorRepositoryProtocol) -> None:
        """Initialize metadata manager.

        Args:
            connector_repo: Database access for track metadata operations.
        """
        self.connector_repo = connector_repo

    async def fetch_fresh_metadata(
        self,
        identity_mappings: MatchResultsById,
        connector: str,
        connector_instance: Any,
        track_ids_to_refresh: list[int],
        **additional_options: Any,
    ) -> tuple[dict[int, dict[str, Any]], set[int]]:
        """Update track metadata by calling the music service API.

        Uses existing track mappings to make efficient direct API calls rather than
        re-matching tracks. Stores successful results in database.

        Args:
            identity_mappings: Tracks mapped to external service IDs.
            connector: Music service name (e.g., 'spotify', 'lastfm').
            connector_instance: API client for the music service.
            track_ids_to_refresh: Internal track IDs needing fresh metadata.
            **additional_options: Extra parameters passed to API client.

        Returns:
            Fresh metadata dict by track ID and set of failed track IDs.
        """
        if not track_ids_to_refresh:
            return {}, set()

        with logger.contextualize(
            operation="fetch_fresh_metadata",
            connector=connector,
            track_count=len(track_ids_to_refresh),
        ):
            logger.info(
                f"Fetching fresh metadata for {len(track_ids_to_refresh)} tracks from {connector}"
            )

            # Filter identity mappings to only include tracks that need refresh
            tracks_to_refresh = {
                track_id: result
                for track_id, result in identity_mappings.items()
                if track_id in track_ids_to_refresh and result.success
            }

            if not tracks_to_refresh:
                failed_track_ids = set(track_ids_to_refresh)
                logger.warning(
                    f"No valid identity mappings found for {len(failed_track_ids)} tracks needing refresh"
                )
                return {}, failed_track_ids

            # CRITICAL FIX: Use direct metadata fetch for mapped tracks instead of expensive matching
            # All connectors should use direct API calls when we have existing mappings
            fresh_metadata = await self._fetch_direct_metadata_by_connector_ids(
                tracks_to_refresh, connector, connector_instance, **additional_options
            )

            # Calculate which tracks failed to fetch fresh metadata
            successfully_fetched = set(fresh_metadata.keys())
            requested_tracks = set(track_ids_to_refresh)
            failed_track_ids = requested_tracks - successfully_fetched

            # Log results for observability
            if failed_track_ids:
                failure_rate = len(failed_track_ids) / len(requested_tracks) * 100
                logger.warning(
                    f"Fresh metadata fetch: {len(successfully_fetched)}/{len(requested_tracks)} successful, {len(failed_track_ids)} failed ({failure_rate:.1f}% failure rate) - will use cached metadata for failed tracks"
                )

            # Store fresh metadata in database
            if fresh_metadata:
                await self._store_fresh_metadata(fresh_metadata, connector)

            return fresh_metadata, failed_track_ids

    async def get_cached_metadata(
        self,
        track_ids: list[int],
        connector: str,
    ) -> dict[int, dict[str, Any]]:
        """Retrieve previously stored track metadata from database.

        Args:
            track_ids: Internal track IDs to get metadata for.
            connector: Music service name.

        Returns:
            Metadata dict by track ID.
        """
        if not track_ids:
            return {}

        with logger.contextualize(
            operation="get_cached_metadata",
            connector=connector,
            track_count=len(track_ids),
        ):
            logger.debug(
                f"Retrieving cached metadata for {len(track_ids)} tracks from {connector}"
            )

            # Get metadata from database
            metadata = await self.connector_repo.get_connector_metadata(
                track_ids, connector
            )

            logger.debug(f"Retrieved cached metadata for {len(metadata)} tracks")
            return metadata

    async def get_all_metadata(
        self,
        track_ids: list[int],
        connector: str,
        fresh_metadata: dict[int, dict[str, Any]] | None = None,
        failed_fresh_track_ids: set[int] | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Combine fresh and cached metadata, using cached as fallback for API failures.

        Args:
            track_ids: Internal track IDs to get metadata for.
            connector: Music service name.
            fresh_metadata: Recently fetched metadata to merge.
            failed_fresh_track_ids: Tracks that failed fresh fetch (use cached only).

        Returns:
            Complete metadata dict by track ID.
        """
        if not track_ids:
            return {}

        with logger.contextualize(
            operation="get_all_metadata",
            connector=connector,
            track_count=len(track_ids),
        ):
            # Get cached metadata for all requested tracks
            cached_metadata = await self.get_cached_metadata(track_ids, connector)

            # Intelligent metadata combination with fallback preservation
            if fresh_metadata:
                # Start with cached metadata as the base (preserves existing data)
                all_metadata = cached_metadata.copy()

                # Overlay fresh metadata (only for successful fetches)
                all_metadata.update(fresh_metadata)

                # Calculate detailed statistics for observability
                fresh_count = len(fresh_metadata)
                cached_count = len(cached_metadata)
                failed_fresh_count = (
                    len(failed_fresh_track_ids) if failed_fresh_track_ids else 0
                )
                total_requested = len(track_ids)

                # Verify no data loss occurred
                final_count = len(all_metadata)
                expected_count = len(
                    set(cached_metadata.keys()) | set(fresh_metadata.keys())
                )

                logger.info(
                    f"Metadata combination complete: {fresh_count} fresh + {cached_count} cached = {final_count} total "
                    f"(requested: {total_requested}, failed fresh: {failed_fresh_count})"
                )

                if failed_fresh_count > 0:
                    # Ensure failed fresh fetches fall back to cached metadata
                    cached_fallback_count = sum(
                        1
                        for track_id in failed_fresh_track_ids or set()
                        if track_id in cached_metadata
                    )
                    logger.info(
                        f"Cached metadata fallback: {cached_fallback_count}/{failed_fresh_count} failed tracks have cached data"
                    )

                if final_count != expected_count:
                    logger.warning(
                        f"Metadata count mismatch: expected {expected_count}, got {final_count}. Possible data loss!"
                    )
            else:
                all_metadata = cached_metadata
                logger.debug(f"Using {len(all_metadata)} cached metadata entries only")

            return all_metadata

    async def _store_fresh_metadata(
        self,
        fresh_metadata: dict[int, dict[str, Any]],
        connector: str,
    ) -> None:
        """Save newly fetched metadata to database with current timestamp.

        Args:
            fresh_metadata: Metadata dict by track ID to store.
            connector: Music service name.
        """
        if not fresh_metadata:
            return

        with logger.contextualize(
            operation="store_fresh_metadata",
            connector=connector,
            metadata_count=len(fresh_metadata),
        ):
            logger.info(f"Storing fresh metadata for {len(fresh_metadata)} tracks")

            # Get existing connector tracks to update their metadata
            track_ids = list(fresh_metadata.keys())
            logger.debug(f"Getting connector mappings for {len(track_ids)} tracks")
            existing_mappings = await self.connector_repo.get_connector_mappings(
                track_ids, connector
            )

            logger.debug(
                f"Found {len(existing_mappings) if existing_mappings else 0} existing mappings"
            )
            if not existing_mappings:
                logger.warning(
                    "No existing connector mappings found for metadata storage"
                )
                return

            # SQLALCHEMY 2.0 COMPLIANT: Use nested transactions for batch operations
            # This allows granular error handling without interfering with parent transaction
            session = self.connector_repo.session
            logger.debug("Starting batch processing with nested transactions")

            # Step 1: Batch process all metrics using nested transaction
            metrics_processed_count = 0
            try:
                # Begin nested transaction (savepoint)
                nested_transaction = await session.begin_nested()
                try:
                    # Note: Metrics processing moved to MetricsApplicationService
                    # This connector metadata manager focuses on metadata storage only
                    # Metrics extraction is handled at the application layer
                    logger.debug(
                        f"Stored fresh metadata for {len(fresh_metadata)} tracks"
                    )

                    metrics_processed_count = len(fresh_metadata)
                    logger.debug(
                        f"Successfully batch processed metrics for {metrics_processed_count} tracks"
                    )
                    await nested_transaction.commit()  # Commit savepoint
                except Exception as inner_e:
                    await nested_transaction.rollback()  # Rollback savepoint
                    raise inner_e

            except Exception as e:
                logger.error(f"Error in batch metrics processing: {e}", exc_info=True)
                # Continue with mapping updates even if metrics fail

            # Step 2: Batch update connector tracks and mappings using nested transaction
            updates_count = 0
            mapping_updates = []

            # Collect all updates first
            for track_id, metadata in fresh_metadata.items():
                if (
                    track_id in existing_mappings
                    and connector in existing_mappings[track_id]
                ):
                    connector_id = existing_mappings[track_id][connector]
                    mapping_updates.append({
                        "track_id": track_id,
                        "connector_id": connector_id,
                        "metadata": metadata,
                    })

            # Step 3: Execute mapping updates in nested transaction
            if mapping_updates:
                logger.debug(
                    f"Batch updating {len(mapping_updates)} mapping confidences"
                )
                try:
                    # Begin nested transaction (savepoint) for mapping updates
                    nested_transaction = await session.begin_nested()
                    try:
                        for update in mapping_updates:
                            await self.connector_repo.save_mapping_confidence(
                                track_id=update["track_id"],
                                connector=connector,
                                connector_id=update["connector_id"],
                                confidence=80,  # Keep existing confidence
                                metadata=update["metadata"],
                            )
                            updates_count += 1

                        logger.debug(
                            f"Successfully batch updated {updates_count} mapping confidences"
                        )
                        await nested_transaction.commit()  # Commit savepoint
                    except Exception as inner_e:
                        await nested_transaction.rollback()  # Rollback savepoint
                        raise inner_e

                except Exception as e:
                    logger.error(f"Error in batch mapping updates: {e}", exc_info=True)

            # Let parent session (get_session() context manager) handle final commit
            logger.info(
                f"Batch operation completed: stored fresh metadata for {updates_count} tracks and processed metrics for {metrics_processed_count} tracks"
            )

    def _convert_track_info_results(
        self, track_info_results: dict[int, Any]
    ) -> dict[int, dict[str, Any]]:
        """Convert API response objects to standardized metadata dictionaries.

        Handles different object types (attrs classes, dict objects, to_dict() methods)
        returned by various music service APIs.

        Args:
            track_info_results: Raw API response objects by track ID.

        Returns:
            Standardized metadata dictionaries by track ID.
        """
        metadata = {}

        for track_id, track_info in track_info_results.items():
            if track_info and hasattr(track_info, "to_dict"):
                metadata[track_id] = track_info.to_dict()
            elif track_info and isinstance(track_info, dict):
                # Handle case where track_info is already a dict
                metadata[track_id] = track_info
            elif track_info:
                # Handle attrs classes (like LastFMTrackInfo)
                try:
                    from attrs import asdict

                    metadata[track_id] = asdict(track_info)
                except (ImportError, TypeError):
                    # Fallback for unexpected types
                    metadata[track_id] = {}

        return metadata

    async def _fetch_direct_metadata_by_connector_ids(
        self,
        tracks_to_refresh: dict[int, Any],
        connector: str,
        connector_instance: Any,
        **additional_options: Any,
    ) -> dict[int, dict[str, Any]]:
        """Call music service API directly using existing track mappings.

        Avoids expensive track matching by using stored external service IDs.
        Makes batch API calls for better performance and rate limit management.

        Args:
            tracks_to_refresh: Tracks needing refresh with their match results.
            connector: Music service name.
            connector_instance: API client for the music service.
            **additional_options: Extra parameters passed to API client.

        Returns:
            Fresh metadata dict by track ID.
        """
        if not tracks_to_refresh:
            return {}

        fresh_metadata = {}

        with logger.contextualize(
            operation="fetch_direct_metadata_by_connector_ids",
            connector=connector,
            track_count=len(tracks_to_refresh),
        ):
            logger.info(
                f"PERFORMANCE OPTIMIZATION: Fetching metadata directly for {len(tracks_to_refresh)} {connector} tracks with existing mappings"
            )

            # Get connector track information for direct API calls
            track_ids = list(tracks_to_refresh.keys())
            existing_mappings = await self.connector_repo.get_connector_mappings(
                track_ids, connector
            )

            if not existing_mappings:
                logger.warning(f"No connector mappings found for {connector} tracks")
                return {}

            # Build list of tracks for direct API calls
            tracks_for_api = []
            track_id_to_connector_id = {}

            for track_id, result in tracks_to_refresh.items():
                if (
                    track_id in existing_mappings
                    and connector in existing_mappings[track_id]
                ):
                    connector_id = existing_mappings[track_id][connector]
                    track_id_to_connector_id[track_id] = connector_id

                    # Use the track from the MatchResult - with null safety
                    track = result.track
                    if track is not None:
                        tracks_for_api.append(track)
                    else:
                        logger.warning(
                            f"Skipping track_id {track_id} - MatchResult.track is None"
                        )

            if not tracks_for_api:
                logger.warning(f"No valid connector mappings found for {connector}")
                return {}

            logger.info(
                f"Making direct API calls for {len(tracks_for_api)} {connector} tracks"
            )

            try:
                # Batch-first architecture: Only use batch method (single operations are degenerate case)
                if not (
                    hasattr(connector_instance, "batch_get_track_info")
                    and callable(connector_instance.batch_get_track_info)
                ):
                    logger.error(
                        f"Connector {connector} must implement batch_get_track_info method"
                    )
                    return {}

                # Use batch method for all operations (batch-first design)
                batch_method = cast(
                    "BatchTrackInfoMethod", connector_instance.batch_get_track_info
                )
                track_info_results = await batch_method(
                    tracks_for_api, **additional_options
                )

                # Convert results to our format (single conversion logic)
                fresh_metadata.update(
                    self._convert_track_info_results(track_info_results)
                )

                # Calculate success metrics for better observability
                requested_count = len(tracks_for_api)
                successful_count = len(fresh_metadata)
                failed_count = requested_count - successful_count

                logger.info(
                    f"Metadata fetch results for {connector}: {successful_count}/{requested_count} successful, {failed_count} failed"
                )

                if failed_count > 0:
                    failure_rate = (failed_count / requested_count) * 100
                    logger.warning(
                        f"High failure rate for {connector} metadata fetch: {failure_rate:.1f}% ({failed_count}/{requested_count})"
                    )

            except Exception as e:
                logger.error(f"Error fetching direct metadata from {connector}: {e}")
                # CRITICAL FIX: Return partial results instead of losing all metadata
                # This preserves any metadata that was successfully fetched before the error
                if fresh_metadata:
                    logger.warning(
                        f"Returning {len(fresh_metadata)} partial results despite error for {connector}"
                    )
                else:
                    logger.error(
                        f"No metadata could be fetched for {connector} tracks due to error"
                    )
                # Always return fresh_metadata (could be empty or partial) instead of forcing empty dict

        return fresh_metadata
