"""Track metrics manager service.

Manages track metrics with database-first caching strategy. Uses track_metrics 
table as primary source, only fetching fresh data when metrics are missing or stale.
"""

from typing import Any

from src.config import get_logger
from src.domain.entities import TrackList
from src.domain.repositories.interfaces import (
    ConnectorRepositoryProtocol,
    MetricsRepositoryProtocol,
    TrackRepositoryProtocol,
)

from .connector_metadata_manager import ConnectorMetadataManager
from .metric_freshness_controller import MetricFreshnessController
from .track_identity_resolver import TrackIdentityResolver

logger = get_logger(__name__)


class TrackMetricsManager:
    """Manages track metrics with database-first caching strategy.

    Process:
    1. Get existing metrics from track_metrics table
    2. Identify tracks missing required metrics
    3. Fetch fresh metadata only for missing/stale tracks
    4. Combine existing + fresh metrics in result
    """

    def __init__(
        self,
        track_repo: TrackRepositoryProtocol,
        connector_repo: ConnectorRepositoryProtocol,
        metrics_repo: MetricsRepositoryProtocol,
    ) -> None:
        """Initialize enricher with repository dependencies.
        
        Args:
            track_repo: Core track repository.
            connector_repo: Connector repository for identity and metadata.
            metrics_repo: Metrics repository for storing calculated metrics.
        """
        self.track_repo = track_repo
        self.connector_repo = connector_repo
        self.metrics_repo = metrics_repo
        self.identity_resolver = TrackIdentityResolver(track_repo, connector_repo)
        self.freshness_controller = MetricFreshnessController(connector_repo)
        self.metadata_manager = ConnectorMetadataManager(connector_repo)

    async def enrich_tracks(
        self,
        track_list: TrackList,
        connector: str,
        connector_instance: Any,
        extractors: dict[str, Any],
        max_age_hours: float | None = None,
        **additional_options: Any,
    ) -> tuple[TrackList, dict[str, dict[int, Any]]]:
        """Enrich tracks with connector metrics, using existing data first.

        Args:
            track_list: Tracks to enrich.
            connector: Connector name.
            connector_instance: Connector implementation.
            extractors: Metric extractors for this connector.
            max_age_hours: Override freshness policy.
            **additional_options: Options forwarded to services.

        Returns:
            Tuple of (enriched_tracklist, metrics_dictionary).
        """
        logger.info(
            f"Starting track metadata enrichment for {len(track_list.tracks)} tracks"
        )

        if not track_list.tracks:
            logger.info("No tracks to enrich")
            return track_list, {}

        # Extract valid tracks with IDs
        valid_tracks = [t for t in track_list.tracks if t.id is not None]

        if not valid_tracks:
            logger.warning("No tracks have database IDs - unable to enrich metadata")
            return track_list, {}

        track_ids = [t.id for t in valid_tracks if t.id is not None]

        with logger.contextualize(
            operation="enrich_tracks", connector=connector, track_count=len(track_ids)
        ):
            logger.info(
                f"Starting track metadata enrichment for {len(track_ids)} tracks"
            )

            # Step 1: Resolve track identities
            identity_mappings = await self.identity_resolver.resolve_track_identities(
                track_list, connector, connector_instance, **additional_options
            )

            logger.info(f"Resolved {len(identity_mappings)} track identities")

            # If no identities could be resolved, return unchanged
            if not identity_mappings:
                logger.warning(
                    "No track identities resolved, returning unchanged tracklist"
                )
                return track_list, {}

            # Get track IDs that have identity mappings
            mapped_track_ids = list(identity_mappings.keys())
            requested_metrics = list(extractors.keys())

            # Step 2: Get existing metrics from track_metrics table
            existing_metrics = await self._get_existing_metrics(
                mapped_track_ids, connector, requested_metrics, max_age_hours
            )

            logger.info(f"Found existing metrics for {sum(len(values) for values in existing_metrics.values())} metric entries")

            # Step 3: Identify tracks missing required metrics
            tracks_needing_refresh = set()
            for metric_name in requested_metrics:
                existing_values = existing_metrics.get(metric_name, {})
                missing_track_ids = [tid for tid in mapped_track_ids if tid not in existing_values]
                tracks_needing_refresh.update(missing_track_ids)
                if missing_track_ids:
                    logger.info(f"Found {len(missing_track_ids)} tracks missing {metric_name} metric")

            # Step 4: Check metadata freshness for tracks needing refresh
            stale_track_ids = []
            if tracks_needing_refresh:
                stale_track_ids = await self.freshness_controller.get_stale_tracks(
                    list(tracks_needing_refresh), connector, max_age_hours
                )
                logger.info(f"Found {len(stale_track_ids)} tracks with stale metadata needing refresh")

            # Step 5: Fetch fresh metadata and extract metrics for stale tracks only
            fresh_metrics = {}
            if stale_track_ids:
                (
                    fresh_metadata,
                    _failed_fresh_track_ids,
                ) = await self.metadata_manager.fetch_fresh_metadata(
                    identity_mappings,
                    connector,
                    connector_instance,
                    stale_track_ids,
                    **additional_options,
                )
                
                if fresh_metadata:
                    logger.info(f"Fetched fresh metadata for {len(fresh_metadata)} tracks")
                    
                    # Extract metrics only for tracks with fresh metadata
                    fresh_metrics = await self._extract_metrics(
                        {tid: identity_mappings[tid] for tid in fresh_metadata},
                        fresh_metadata,
                        extractors
                    )
                    
                    # Persist fresh metrics to database
                    await self._persist_metrics_to_database(fresh_metrics, connector)

            # Step 6: Combine existing metrics with fresh metrics
            combined_metrics = {}
            for metric_name in requested_metrics:
                combined_values = {}
                
                # Add existing metrics
                if metric_name in existing_metrics:
                    combined_values.update(existing_metrics[metric_name])
                    
                # Add fresh metrics (will override existing if same track)
                if metric_name in fresh_metrics:
                    combined_values.update(fresh_metrics[metric_name])
                    
                if combined_values:
                    combined_metrics[metric_name] = combined_values

            logger.info(f"Combined metrics: {[(name, len(values)) for name, values in combined_metrics.items()]}")

            # Step 7: Attach combined metrics to tracklist
            enriched_tracklist = self._attach_metrics_to_tracklist(track_list, combined_metrics)

            logger.info(
                f"Successfully enriched tracklist with {sum(len(values) for values in combined_metrics.values())} total metric values"
            )

            return enriched_tracklist, combined_metrics

    async def _extract_metrics(
        self,
        identity_mappings: dict[int, Any],
        all_metadata: dict[int, dict[str, Any]],
        extractors: dict[str, Any],
    ) -> dict[str, dict[int, Any]]:
        """Extract metrics from metadata using configured extractors.

        Args:
            identity_mappings: Track identity mappings.
            all_metadata: Complete metadata for all tracks.
            extractors: Metric extractors for this connector.

        Returns:
            Dictionary mapping metric names to track_id -> value mappings.
        """
        
        metrics = {}

        for metric_name, extractor in extractors.items():
            values = {}

            for track_id, identity_result in identity_mappings.items():
                if not identity_result.success or track_id not in all_metadata:
                    continue

                # Get metadata for this track
                track_metadata = all_metadata[track_id]

                try:
                    # Create a result object with metadata for extraction
                    result_with_metadata = identity_result.__class__(
                        track=identity_result.track,
                        success=identity_result.success,
                        connector_id=identity_result.connector_id,
                        confidence=identity_result.confidence,
                        match_method=identity_result.match_method,
                        service_data=track_metadata,
                        evidence=identity_result.evidence,
                    )

                    # Extract value using the configured extractor
                    value = extractor(result_with_metadata)

                    if value is not None:
                        values[track_id] = value
                        logger.debug(
                            f"Added {metric_name}={value} for track_id={track_id}"
                        )
                    else:
                        logger.warning(f"Extracted None value for {metric_name} on track {track_id}")

                except Exception as e:
                    logger.error(
                        f"Failed to extract metric '{metric_name}' for track {track_id}: {e}"
                    )

            if values:
                metrics[metric_name] = values
                logger.info(
                    f"Extracted {len(values)} values for metric '{metric_name}'"
                )
            else:
                logger.warning(f"No values extracted for metric '{metric_name}'")

        return metrics

    async def _get_existing_metrics(
        self,
        track_ids: list[int],
        connector: str, 
        requested_metrics: list[str],
        max_age_hours: float | None = None,
    ) -> dict[str, dict[int, Any]]:
        """Get existing metrics from track_metrics table.
        
        Args:
            track_ids: Track IDs to get metrics for.
            connector: Connector name.
            requested_metrics: List of metric names to retrieve.
            max_age_hours: Maximum age of metrics to consider fresh.
            
        Returns:
            Dictionary mapping metric names to track_id -> value mappings.
        """
        if not track_ids or not requested_metrics:
            return {}
            
        logger.debug(f"Getting existing metrics for {len(track_ids)} tracks: {requested_metrics}")
        
        existing_metrics = {}
        
        # Get each requested metric type from the track_metrics table
        for metric_name in requested_metrics:
            try:
                metric_values = await self.metrics_repo.get_track_metrics(
                    track_ids=track_ids,
                    metric_type=metric_name,
                    connector=connector,
                    max_age_hours=max_age_hours or 24.0,
                )
                
                if metric_values:
                    existing_metrics[metric_name] = metric_values
                    logger.debug(f"Found {len(metric_values)} existing values for {metric_name}")
                else:
                    logger.debug(f"No existing values found for {metric_name}")
                    
            except Exception as e:
                logger.error(f"Error getting existing metrics for {metric_name}: {e}")
                
        logger.debug(f"Retrieved existing metrics: {list(existing_metrics.keys())}")
        return existing_metrics

    def _attach_metrics_to_tracklist(
        self,
        track_list: TrackList,
        metrics: dict[str, dict[int, Any]],
    ) -> TrackList:
        """Attach metrics to tracklist metadata.

        Args:
            track_list: Original tracklist.
            metrics: Extracted metrics.

        Returns:
            Tracklist with metrics attached to metadata.
        """
        if not metrics:
            logger.debug("No metrics to attach to tracklist")
            return track_list

        # Merge with existing metrics
        current_metrics = track_list.metadata.get("metrics", {})
        combined_metrics = {**current_metrics, **metrics}

        # Return new tracklist with updated metadata
        return track_list.with_metadata("metrics", combined_metrics)

    async def _persist_metrics_to_database(
        self,
        metrics: dict[str, dict[int, Any]],
        connector: str,
    ) -> None:
        """Persist extracted metrics to the database for future use.

        Args:
            metrics: Extracted metrics mapping metric names to track_id -> value mappings.
            connector: Connector name for the metrics.
        """
        if not metrics:
            return

        with logger.contextualize(
            operation="persist_metrics",
            connector=connector,
            metric_count=sum(len(values) for values in metrics.values()),
        ):
            # Build batch of metrics for database storage
            metric_batch = []

            for metric_name, track_values in metrics.items():
                for track_id, value in track_values.items():
                    try:
                        # Convert value to float for database storage
                        float_value = float(value)
                        metric_batch.append((
                            track_id,
                            connector,
                            metric_name,
                            float_value,
                        ))
                    except (ValueError, TypeError):
                        logger.warning(
                            f"Could not convert metric value to float: {metric_name}={value} for track {track_id}"
                        )

            if not metric_batch:
                logger.debug("No valid metrics to persist")
                return

            logger.info(f"Persisting {len(metric_batch)} metrics to database")

            try:
                # Use the track repositories metrics store to persist
                await self.metrics_repo.save_track_metrics(metric_batch)
                logger.info(
                    f"Successfully persisted {len(metric_batch)} metrics to database"
                )

                # Log sample of what was stored for debugging
                sample_metrics = metric_batch[:3]
                for track_id, _connector, metric_name, value in sample_metrics:
                    logger.debug(
                        f"Stored metric: track_id={track_id}, {metric_name}={value}"
                    )

            except Exception as e:
                logger.error(f"Failed to persist metrics to database: {e}")
                # Don't raise the exception - metrics persistence shouldn't break the workflow
                # The metrics are still attached to the tracklist for immediate use
