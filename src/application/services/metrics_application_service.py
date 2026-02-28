"""Service for resolving, caching, and persisting track metrics from external connectors.

Handles the complete lifecycle of track metrics: checking cache for fresh data,
fetching missing metrics from external APIs, converting values to standardized
formats, and persisting results for future use.
"""

from typing import TYPE_CHECKING, Any

from attrs import define, field

from src.application.utilities.enhanced_database_batch_processor import (
    EnhancedDatabaseBatchProcessor,
)
from src.config import get_logger
from src.domain.entities.track import Track
from src.domain.repositories import UnitOfWorkProtocol

if TYPE_CHECKING:
    from src.application.workflows.protocols import (
        MetricConfigProvider,
        TrackMetadataConnector,
    )

type _MetricsTuple = tuple[int, str, str, float | int | bool]

logger = get_logger(__name__)


def _default_metric_config() -> MetricConfigProvider:
    """Provide default MetricConfigProvider from infrastructure."""
    from src.infrastructure.connectors._shared.metric_registry import (
        MetricConfigProviderImpl,
    )

    return MetricConfigProviderImpl()


@define(slots=True)
class MetricsApplicationService:
    """Coordinates metric resolution with intelligent caching and batch processing.

    Resolves track metrics by first checking cached values, then fetching missing
    data from external connectors, and persisting results. Optimizes performance
    through freshness-based caching and batch operations for large datasets.
    """

    _metric_config: MetricConfigProvider = field(factory=_default_metric_config)

    async def get_external_track_metrics(
        self,
        track_ids: list[int],
        connector: str,
        metric_names: list[str],
        uow: UnitOfWorkProtocol,
        connector_instance: TrackMetadataConnector | None = None,
    ) -> tuple[dict[str, dict[int, Any]], dict[str, set[int]]]:
        """Get track metrics from external APIs using cache-first strategy.

        This is the primary method for retrieving track metrics. It handles multiple
        metrics efficiently by using cached values when available and only fetching
        fresh data when needed. Uses the registered connector metric configurations
        for field mapping and freshness policies.

        Args:
            track_ids: Internal track IDs to get metrics for.
            connector: External connector name ('spotify', 'lastfm', etc.).
            metric_names: List of metric names to retrieve (e.g., ['spotify_popularity', 'lastfm_user_playcount']).
            uow: Unit of work for database transaction management.
            connector_instance: Optional connector instance for fresh metadata fetching.

        Returns:
            Tuple of (metrics_dict, fresh_ids_dict) where:
            - metrics_dict: Dictionary mapping metric names to track_id -> value mappings.
              Example: {'spotify_popularity': {1: 85, 2: 92}, 'lastfm_user_playcount': {1: 75, 2: 120}}
            - fresh_ids_dict: Dictionary mapping metric names to sets of track IDs that
              were freshly fetched (not from cache) in this session.
        """
        if not track_ids or not metric_names:
            return {}, {}

        logger.info(
            f"Getting external track metrics for {len(track_ids)} tracks",
            connector=connector,
            metric_names=metric_names,
            track_count=len(track_ids),
        )

        # Validate that all requested metrics are supported by this connector
        available_metrics = self._metric_config.get_connector_metrics(connector)
        unsupported_metrics = [m for m in metric_names if m not in available_metrics]
        if unsupported_metrics:
            logger.warning(
                f"Connector {connector} does not support metrics: {unsupported_metrics}. "
                + f"Available metrics: {available_metrics}"
            )
            # Filter to only supported metrics
            metric_names = [m for m in metric_names if m in available_metrics]

        if not metric_names:
            logger.warning(f"No supported metrics found for connector {connector}")
            return {}, {}

        # Build field map from registered metric configurations
        field_map: dict[str, str] = {}
        for metric_name in metric_names:
            field_name = self._metric_config.get_field_name(metric_name)
            if field_name:
                field_map[metric_name] = field_name
            else:
                logger.warning(f"No field mapping found for {metric_name}")

        if not field_map:
            logger.warning("No valid field mappings found for any requested metrics")
            return {}, {}

        # PRE-FETCH STRATEGY: SQLite-safe concurrent processing
        import asyncio

        result: dict[str, dict[int, Any]] = {}
        fresh_ids_per_metric: dict[str, set[int]] = {}

        # Phase 1: Single database transaction to identify missing data
        missing_tracks_per_metric: dict[str, list[int]] = {}
        cached_values_per_metric: dict[str, dict[int, Any]] = {}

        async with uow:
            for metric_name in field_map:
                # Check cache for fresh metrics
                max_age_hours = self._metric_config.get_metric_freshness(metric_name)
                metrics_repo = uow.get_metrics_repository()

                cached_values = await metrics_repo.get_track_metrics(
                    track_ids,
                    metric_type=metric_name,
                    connector=connector,
                    max_age_hours=max_age_hours,
                )

                # Find tracks needing fresh data
                missing_ids = [tid for tid in track_ids if tid not in cached_values]

                if missing_ids:
                    missing_tracks_per_metric[metric_name] = missing_ids
                    logger.info(
                        f"Found {len(missing_ids)} tracks missing {metric_name} data"
                    )

                cached_values_per_metric[metric_name] = cached_values

        # Phase 2: Concurrent API operations (with fresh database sessions)
        if missing_tracks_per_metric:
            # Collect results from concurrent metric resolution
            metric_results: list[tuple[str, dict[int, Any]]] = []

            async def resolve_single_metric_no_db(
                metric_name: str, field_value: str
            ) -> None:
                """Resolve a single metric and append to shared results list."""
                try:
                    missing_ids = missing_tracks_per_metric.get(metric_name, [])
                    if not missing_ids:
                        return

                    # Create fresh UOW for concurrent database operations
                    from src.infrastructure.persistence.database.db_connection import (
                        get_session,
                    )
                    from src.infrastructure.persistence.repositories.factories import (
                        get_unit_of_work,
                    )

                    async with get_session() as fresh_session:
                        fresh_uow = get_unit_of_work(fresh_session)
                        fresh_values = await self._resolve_metrics_no_db(
                            track_ids=missing_ids,
                            metric_name=metric_name,
                            connector=connector,
                            field_name=field_value,
                            uow=fresh_uow,
                            connector_instance=connector_instance,
                        )
                        metric_results.append((metric_name, fresh_values))
                except Exception as exc:
                    logger.error(f"Error resolving metric {metric_name}: {exc}")

            # Execute all API calls concurrently with structured concurrency
            metrics_to_resolve = [
                (mn, fv)
                for mn, fv in field_map.items()
                if mn in missing_tracks_per_metric
            ]

            if metrics_to_resolve:
                async with asyncio.TaskGroup() as tg:
                    for metric_name, field_value in metrics_to_resolve:
                        _ = tg.create_task(
                            resolve_single_metric_no_db(metric_name, field_value)
                        )

                # Collect fresh data for bulk save
                all_metrics_to_save: list[tuple[int, str, str, float | int | bool]] = []
                for metric_name, fresh_values in metric_results:
                    for track_id, value in fresh_values.items():
                        if value is not None:
                            try:
                                # Preserve original data types
                                if isinstance(value, (bool, int, float)):
                                    converted_value = value
                                else:
                                    converted_value = float(value)

                                all_metrics_to_save.append((
                                    track_id,
                                    connector,
                                    metric_name,
                                    converted_value,
                                ))

                                # Update cached values
                                if metric_name not in cached_values_per_metric:
                                    cached_values_per_metric[metric_name] = {}
                                cached_values_per_metric[metric_name][track_id] = value

                                # Track as freshly fetched
                                if metric_name not in fresh_ids_per_metric:
                                    fresh_ids_per_metric[metric_name] = set()
                                fresh_ids_per_metric[metric_name].add(track_id)

                            except ValueError, TypeError:
                                logger.warning(
                                    f"Cannot convert {value} for {metric_name}"
                                )

                # Phase 3: Single database transaction to bulk save
                if all_metrics_to_save:
                    async with uow:
                        metrics_repo = uow.get_metrics_repository()
                        saved_count = await metrics_repo.save_track_metrics(
                            all_metrics_to_save
                        )
                        await uow.commit()
                        logger.info(f"Bulk saved {saved_count} new metrics")

        # Combine cached and fresh values
        for metric_name in field_map:
            metric_values = cached_values_per_metric.get(metric_name, {})
            if metric_values:
                result[metric_name] = metric_values
                logger.debug(f"Retrieved {len(metric_values)} values for {metric_name}")
            else:
                logger.debug(f"No values retrieved for {metric_name}")

        logger.info(
            f"Successfully retrieved {len(result)} metric types with "
            + f"{sum(len(values) for values in result.values())} total values "
            + f"({sum(len(ids) for ids in fresh_ids_per_metric.values())} freshly fetched)"
        )

        return result, fresh_ids_per_metric

    async def extract_track_metrics(
        self, tracks: list[Track], uow: UnitOfWorkProtocol
    ) -> None:
        """Extract and save metrics from track connector metadata.

        Groups tracks by connector type and batch processes their metadata
        to extract structured metrics for analysis.

        Args:
            tracks: Tracks with potential connector metadata
            uow: Transaction manager for database operations
        """
        if not tracks:
            return

        for (
            connector,
            available_metrics,
        ) in self._metric_config.get_all_connectors_metrics().items():
            tracks_with_metadata: list[Track] = []
            fresh_metadata: dict[int, dict[str, Any]] = {}

            for track in tracks:
                if (
                    track.id
                    and track.connector_metadata
                    and connector in track.connector_metadata
                ):
                    tracks_with_metadata.append(track)
                    fresh_metadata[track.id] = track.connector_metadata[connector]

            if fresh_metadata:
                logger.info(
                    f"Extracting {len(available_metrics)} metrics from {connector} for {len(tracks_with_metadata)} tracks",
                    connector=connector,
                    metrics=available_metrics,
                    track_count=len(tracks_with_metadata),
                )

                await self.batch_process_fresh_metadata(
                    fresh_metadata=fresh_metadata,
                    connector=connector,
                    available_metrics=available_metrics,
                    field_map=self._metric_config.get_all_field_mappings(),
                    uow=uow,
                )

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

        all_metrics_batch: list[tuple[int, str, str, float]] = []

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
                except ValueError, TypeError:
                    logger.warning(f"Cannot convert {value} to float for {metric_name}")

        # Batch save all metrics using unified BatchProcessor
        if all_metrics_batch:
            metrics_repo = uow.get_metrics_repository()

            # Create enhanced database batch processor with progress tracking
            batch_processor = EnhancedDatabaseBatchProcessor[
                _MetricsTuple, int
            ](
                batch_size=10,  # Small batch size to prevent SQLite locks
                retry_count=3,  # Simple retry for database deadlock scenarios
                retry_base_delay=1.0,  # Basic retry delay, no complex exponential backoff needed
                logger_instance=logger,
            )

            async def save_metrics_batch(metrics_batch: list[_MetricsTuple]) -> int:
                """Save a batch of metrics to the database."""
                return await metrics_repo.save_track_metrics(metrics_batch)

            # Process using EnhancedBatchProcessor (it handles batching internally)
            batch_results = await batch_processor.process(
                items=all_metrics_batch,
                process_func=save_metrics_batch,
                operation_description=f"Saving {len(all_metrics_batch)} metrics to database",
                source="metrics_service",
                batch_type="database_metrics_save",
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
        external_ids: list[str] = []
        external_id_to_track_id: dict[str, int] = {}

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
        field_values: dict[int, Any] = {}
        for track_id, metadata in fresh_metadata.items():
            if metadata:
                field_value = metadata.get(field_name)
                if field_value is not None:
                    field_values[track_id] = field_value

        logger.info(
            f"Successfully extracted {len(field_values)} {field_name} values from {connector} API",
            extracted_count=len(field_values),
            requested_count=len(track_ids),
        )

        return field_values

    async def _resolve_metrics_no_db(
        self,
        track_ids: list[int],
        metric_name: str,
        connector: str,
        field_name: str,
        uow: UnitOfWorkProtocol,
        connector_instance: TrackMetadataConnector | None = None,
    ) -> dict[int, Any]:
        """Resolve metrics from external API without database transactions.

        This method handles the API portion of metric resolution, designed for
        concurrent execution without SQLite lock contention. Database operations
        are handled separately by the caller.

        Args:
            track_ids: Internal track IDs needing fresh metadata.
            metric_name: Name of the metric to resolve.
            connector: External connector name.
            field_name: Field name in connector metadata.
            uow: Unit of work for read-only database access.
            connector_instance: Connector instance for API calls.

        Returns:
            Dictionary mapping track IDs to metric values.
        """
        if not track_ids or not connector_instance:
            return {}

        logger.debug(
            f"Fetching {metric_name} for {len(track_ids)} tracks from {connector} API",
            metric_name=metric_name,
            connector=connector,
            track_count=len(track_ids),
        )

        # Read-only database operations (safe for concurrency)
        async with uow:
            # Get tracks for API call
            track_repo = uow.get_track_repository()
            tracks_dict = await track_repo.find_tracks_by_ids(track_ids)
            tracks = list(tracks_dict.values())

        if not tracks:
            logger.warning(f"No Track objects found for IDs: {track_ids}")
            return {}

        # API call (no database involvement - safe for concurrency)
        try:
            fresh_metadata = await connector_instance.get_external_track_data(tracks)
        except Exception as e:
            logger.error(f"Failed to fetch {metric_name} from {connector} API: {e}")
            return {}

        if not fresh_metadata:
            logger.warning(f"No {metric_name} metadata returned from {connector} API")
            return {}

        # Extract field values from metadata
        field_values: dict[int, Any] = {}
        for track_id, metadata in fresh_metadata.items():
            if metadata:
                field_value = metadata.get(field_name)
                if field_value is not None:
                    field_values[track_id] = field_value

        logger.debug(
            f"Extracted {len(field_values)} {metric_name} values from {connector} API",
            extracted_count=len(field_values),
            requested_count=len(track_ids),
        )

        return field_values
