"""Service for resolving, caching, and persisting track metrics from external connectors.

Handles the complete lifecycle of track metrics: checking cache for fresh data,
fetching missing metrics from external APIs, converting values to standardized
formats, and persisting results for future use.
"""

from typing import Any

from attrs import define

from src.application.utilities.database_batch_processor import DatabaseBatchProcessor
from src.config import get_logger
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.metrics import (
    get_connector_metrics,
    get_field_name,
    get_metric_freshness,
)
from src.infrastructure.connectors.protocols import TrackMetadataConnector

logger = get_logger(__name__)


@define(slots=True)
class MetricsApplicationService:
    """Coordinates metric resolution with intelligent caching and batch processing.

    Resolves track metrics by first checking cached values, then fetching missing
    data from external connectors, and persisting results. Optimizes performance
    through freshness-based caching and batch operations for large datasets.
    """

    async def resolve_metrics(
        self,
        track_ids: list[int],
        metric_name: str,
        connector: str,
        field_map: dict[str, str],
        uow: UnitOfWorkProtocol,
        connector_instance: TrackMetadataConnector | None = None,
    ) -> dict[int, Any]:
        """Resolves metric values for multiple tracks with cache-first strategy.

        Checks cache for fresh metric values, fetches missing data from the specified
        connector, converts values to float format, and persists new metrics.
        Returns complete set of metric values for all requested tracks.

        Args:
            track_ids: Internal track IDs to resolve metrics for.
            metric_name: Name of the metric to resolve (e.g., 'danceability').
            connector: External connector name ('spotify', 'lastfm', etc.).
            field_map: Maps metric names to connector field names.
            uow: Unit of work for database transaction management.

        Returns:
            Dictionary mapping track IDs to their metric values.
        """
        if not track_ids:
            return {}

        logger.info(
            f"Resolving {metric_name} metrics for {len(track_ids)} tracks",
            connector=connector,
            metric_name=metric_name,
            track_count=len(track_ids),
        )

        async with uow:
            # Step 1: Get cached metrics that aren't stale
            max_age_hours = get_metric_freshness(metric_name)
            metrics_repo = uow.get_metrics_repository()

            cached_values = await metrics_repo.get_track_metrics(
                track_ids,
                metric_type=metric_name,
                connector=connector,
                max_age_hours=max_age_hours,
            )

            # Step 2: Find tracks needing fresh data
            missing_ids = [tid for tid in track_ids if tid not in cached_values]

            if not missing_ids:
                logger.info(f"All {len(track_ids)} metrics found in cache")
                return cached_values

            logger.info(
                f"Found {len(missing_ids)} tracks with missing {metric_name} data",
                track_count=len(track_ids),
                missing_count=len(missing_ids),
                missing_sample=missing_ids[:5],
            )

            # Step 3: Get field name from mapping
            field_name = field_map.get(metric_name)
            logger.debug(
                f"Field mapping: {metric_name} -> {field_name} (field_map: {field_map})"
            )
            if not field_name:
                logger.warning(f"No field mapping for {metric_name}")
                return cached_values

            # Step 4: Retrieve metadata for missing tracks
            connector_repo = uow.get_connector_repository()
            metadata = await connector_repo.get_connector_metadata(
                missing_ids, connector, field_name
            )

            # Step 4.5: For tracks without stored metadata or with null field values, fetch from API
            tracks_without_metadata = [
                tid
                for tid in missing_ids
                if tid not in metadata or metadata.get(tid) is None
            ]
            if tracks_without_metadata:
                logger.info(
                    f"Fetching fresh metadata for {len(tracks_without_metadata)} tracks from {connector} API",
                    missing_metadata_count=len(tracks_without_metadata),
                    missing_metadata_sample=tracks_without_metadata[:5],
                )

                # Fetch fresh metadata from the external API
                fresh_metadata = await self._fetch_fresh_metadata(
                    track_ids=tracks_without_metadata,
                    connector=connector,
                    field_name=field_name,
                    uow=uow,
                    connector_instance=connector_instance,
                )

                # Merge fresh metadata with existing metadata
                metadata.update(fresh_metadata)

            # Step 5: Extract and convert metric values (preserve data types)
            metrics_to_save = []
            for track_id, value in metadata.items():
                if value is not None and not isinstance(value, dict):
                    try:
                        # Preserve original data types instead of forcing to float
                        if isinstance(value, (bool, int, float)):
                            # Keep booleans, integers, and floats as-is
                            converted_value = value
                            logger.debug(
                                f"Preserved {type(value).__name__} value {value} for {metric_name}"
                            )
                        else:
                            # Convert strings and other types to float (fallback)
                            converted_value = float(value)
                            logger.debug(
                                f"Converted {type(value).__name__} value {value} to float for {metric_name}"
                            )

                        metrics_to_save.append((
                            track_id,
                            connector,
                            metric_name,
                            converted_value,
                        ))
                        cached_values[track_id] = value
                    except (ValueError, TypeError):
                        logger.warning(
                            f"Cannot convert {value} to numeric type for {metric_name}"
                        )

            # Step 6: Persist new metrics
            if metrics_to_save:
                saved_count = await metrics_repo.save_track_metrics(metrics_to_save)
                await uow.commit()
                logger.info(f"Saved {saved_count} new metrics for {metric_name}")

        return cached_values

    async def get_external_track_metrics(
        self,
        track_ids: list[int],
        connector: str,
        metric_names: list[str],
        uow: UnitOfWorkProtocol,
        connector_instance: TrackMetadataConnector | None = None,
    ) -> dict[str, dict[int, Any]]:
        """Get track metrics from external APIs using cache-first strategy.

        This is the primary method for retrieving track metrics. It handles multiple
        metrics efficiently by using cached values when available and only fetching
        fresh data when needed. Uses the registered connector metric configurations
        for field mapping and freshness policies.

        Args:
            track_ids: Internal track IDs to get metrics for.
            connector: External connector name ('spotify', 'lastfm', etc.).
            metric_names: List of metric names to retrieve (e.g., ['popularity', 'danceability']).
            uow: Unit of work for database transaction management.
            connector_instance: Optional connector instance for fresh metadata fetching.

        Returns:
            Dictionary mapping metric names to track_id -> value mappings.
            Example: {'popularity': {1: 85, 2: 92}, 'danceability': {1: 0.75, 2: 0.82}}
        """
        if not track_ids or not metric_names:
            return {}

        logger.info(
            f"Getting external track metrics for {len(track_ids)} tracks",
            connector=connector,
            metric_names=metric_names,
            track_count=len(track_ids),
        )

        # Validate that all requested metrics are supported by this connector
        available_metrics = get_connector_metrics(connector)
        unsupported_metrics = [m for m in metric_names if m not in available_metrics]
        if unsupported_metrics:
            logger.warning(
                f"Connector {connector} does not support metrics: {unsupported_metrics}. "
                f"Available metrics: {available_metrics}"
            )
            # Filter to only supported metrics
            metric_names = [m for m in metric_names if m in available_metrics]

        if not metric_names:
            logger.warning(f"No supported metrics found for connector {connector}")
            return {}

        # Build field map from registered metric configurations
        field_map = {}
        for metric_name in metric_names:
            field_name = get_field_name(metric_name)
            if field_name:
                field_map[metric_name] = field_name
            else:
                logger.warning(f"No field mapping found for {metric_name}")

        if not field_map:
            logger.warning("No valid field mappings found for any requested metrics")
            return {}

        # Resolve each metric using the existing cache-first logic
        result = {}
        for metric_name in field_map:
            metric_values = await self.resolve_metrics(
                track_ids=track_ids,
                metric_name=metric_name,
                connector=connector,
                field_map={
                    metric_name: field_map[metric_name]
                },  # Single metric field map
                uow=uow,
                connector_instance=connector_instance,
            )

            if metric_values:
                result[metric_name] = metric_values
                logger.debug(f"Retrieved {len(metric_values)} values for {metric_name}")
            else:
                logger.debug(f"No values retrieved for {metric_name}")

        logger.info(
            f"Successfully retrieved {len(result)} metric types with "
            f"{sum(len(values) for values in result.values())} total values"
        )

        return result

    async def batch_process_fresh_metadata(
        self,
        fresh_metadata: dict[int, dict[str, Any]],
        connector: str,
        available_metrics: list[str],
        field_map: dict[str, str],
        uow: UnitOfWorkProtocol,
    ) -> int:
        """Efficiently processes metrics from fresh metadata in batches.

        Extracts and persists all available metrics from pre-fetched metadata
        for multiple tracks. Uses small batch sizes to prevent database locks
        when processing large datasets.

        Args:
            fresh_metadata: Dictionary of track_id -> metadata mappings.
            connector: Connector name the metadata came from.
            available_metrics: List of metric names this connector supports.
            field_map: Maps metric names to connector field names.
            uow: Unit of work for database transaction management.

        Returns:
            Number of individual metrics successfully processed and saved.
        """
        if not fresh_metadata or not available_metrics:
            return 0

        logger.info(
            f"Batch processing {len(fresh_metadata)} fresh metadata entries",
            connector=connector,
            track_count=len(fresh_metadata),
        )

        all_metrics_batch = []

        # Extract all metrics for all tracks
        for track_id, track_metadata in fresh_metadata.items():
            for metric_name in available_metrics:
                field_name = field_map.get(metric_name)
                if not field_name:
                    continue

                # Extract and convert value
                value = track_metadata.get(field_name)
                if value is None:
                    continue

                try:
                    float_value = float(value)
                    all_metrics_batch.append((
                        track_id,
                        connector,
                        metric_name,
                        float_value,
                    ))
                except (ValueError, TypeError):
                    logger.warning(f"Cannot convert {value} to float for {metric_name}")

        # Batch save all metrics using unified BatchProcessor
        if all_metrics_batch:
            metrics_repo = uow.get_metrics_repository()

            # Create database batch processor optimized for bulk database operations
            batch_processor = DatabaseBatchProcessor[
                list, int
            ](
                batch_size=10,  # Small batch size to prevent SQLite locks
                retry_count=3,  # Simple retry for database deadlock scenarios
                retry_base_delay=1.0,  # Basic retry delay, no complex exponential backoff needed
                logger_instance=logger,
            )

            async def save_metrics_batch(metrics_batch: list) -> int:
                """Save a batch of metrics to the database."""
                return await metrics_repo.save_track_metrics(metrics_batch)

            # Process using BatchProcessor (it handles batching internally)
            batch_results = await batch_processor.process(
                items=all_metrics_batch,
                process_func=save_metrics_batch,
                progress_description=f"Saving {len(all_metrics_batch)} metrics to database",
            )

            saved_count = sum(batch_results)
            logger.info(
                f"Batch saved {saved_count} metrics for {len(fresh_metadata)} tracks",
                connector=connector,
            )
            return saved_count

        return 0

    async def _fetch_fresh_metadata(
        self,
        track_ids: list[int],
        connector: str,
        field_name: str,
        uow: UnitOfWorkProtocol,
        connector_instance: TrackMetadataConnector | None = None,
    ) -> dict[int, Any]:
        """Fetch fresh metadata from external API for tracks missing stored data.

        Args:
            track_ids: Internal track IDs needing fresh metadata.
            connector: External connector name ('spotify', 'lastfm', etc.).
            field_name: Specific field to extract from metadata.
            uow: Unit of work for database access.
            connector_instance: Connector instance for API calls.

        Returns:
            Dictionary mapping track_id to extracted field values.
        """
        if not track_ids or not connector_instance:
            return {}

        logger.info(
            f"Fetching fresh metadata for {len(track_ids)} tracks from {connector} API",
            connector=connector,
            field_name=field_name,
            track_count=len(track_ids),
        )

        # Step 1: Get connector mappings (track_id -> external_id)
        connector_repo = uow.get_connector_repository()
        mappings = await connector_repo.get_connector_mappings(
            track_ids=track_ids,
            connector=connector,
        )

        if not mappings:
            logger.warning(f"No {connector} mappings found for any tracks")
            return {}

        # Step 2: Extract external IDs and create reverse mapping
        external_ids = []
        external_id_to_track_id = {}

        for track_id, connector_mappings in mappings.items():
            external_id = connector_mappings.get(connector)
            if external_id:
                external_ids.append(external_id)
                external_id_to_track_id[external_id] = track_id

        if not external_ids:
            logger.warning(f"No {connector} external IDs found in mappings")
            return {}

        logger.info(f"Found {len(external_ids)} {connector} IDs to fetch")

        # Step 3: Get Track domain objects for API call
        track_repo = uow.get_track_repository()
        tracks_dict = await track_repo.find_tracks_by_ids(track_ids)
        tracks = list(tracks_dict.values())

        if not tracks:
            logger.warning(f"No Track objects found for IDs: {track_ids}")
            return {}

        # Step 4: Fetch fresh metadata using unified protocol interface
        try:
            # Use the unified TrackMetadataConnector protocol
            fresh_metadata = await connector_instance.get_external_track_data(tracks)

        except Exception as e:
            logger.error(f"Failed to fetch metadata from {connector} API: {e}")
            return {}

        if not fresh_metadata:
            logger.warning(f"No metadata returned from {connector} API")
            return {}

        # Step 5: Extract field values from metadata (already keyed by track.id)
        field_values = {}
        for track_id, metadata in fresh_metadata.items():
            if metadata:
                field_value = metadata.get(field_name)
                if field_value is not None:
                    field_values[track_id] = field_value
                else:
                    # DEBUG: Log what fields are actually available
                    logger.debug(
                        f"Field '{field_name}' not found in metadata for track {track_id}. Available fields: {list(metadata.keys())}"
                    )

        logger.info(
            f"Successfully extracted {len(field_values)} {field_name} values from {connector} API",
            extracted_count=len(field_values),
            requested_count=len(track_ids),
        )

        return field_values
