"""Service for resolving, caching, and persisting track metrics from external connectors.

Handles the complete lifecycle of track metrics: checking cache for fresh data,
fetching missing metrics from external APIs, converting values to standardized
formats, and persisting results for future use.
"""

# pyright: reportAny=false
# Legitimate Any: use case results, OperationResult metadata, metric values

from typing import TYPE_CHECKING, Any
from uuid import UUID

from attrs import define

from src.application.services.sub_operation_progress import (
    complete_sub_operation,
    create_sub_operation,
)
from src.application.utilities.enhanced_database_batch_processor import (
    EnhancedDatabaseBatchProcessor,
)
from src.config import get_logger
from src.domain.entities.progress import OperationStatus
from src.domain.entities.track import Track
from src.domain.repositories import UnitOfWorkProtocol

if TYPE_CHECKING:
    from src.application.connector_protocols import TrackMetadataConnector
    from src.application.services.progress_manager import AsyncProgressManager
    from src.application.workflows.protocols import MetricConfigProvider

type _MetricsTuple = tuple[UUID, str, str, float | int | bool]

logger = get_logger(__name__)


@define(slots=True)
class MetricsApplicationService:
    """Coordinates metric resolution with intelligent caching and batch processing.

    Resolves track metrics by first checking cached values, then fetching missing
    data from external connectors, and persisting results. Optimizes performance
    through freshness-based caching and batch operations for large datasets.
    """

    metric_config: MetricConfigProvider

    @staticmethod
    def _extract_metrics_from_metadata(
        fresh_metadata: dict[UUID, dict[str, Any]],
        metric_names: list[str],
        field_map: dict[str, str],
        connector: str,
    ) -> list[_MetricsTuple]:
        """Extract typed metric tuples from raw connector metadata.

        Consolidates the value extraction + type conversion logic used by both
        get_external_track_metrics() and batch_process_fresh_metadata().
        Preserves bool/int types instead of coercing everything to float.
        """
        results: list[_MetricsTuple] = []
        for track_id, track_metadata in fresh_metadata.items():
            for metric_name in metric_names:
                field_name = field_map.get(metric_name)
                if not field_name:
                    continue
                value = track_metadata.get(field_name)
                if value is None:
                    continue
                try:
                    if isinstance(value, (bool, int, float)):
                        converted = value
                    else:
                        converted = float(value)
                    results.append((track_id, connector, metric_name, converted))
                except ValueError, TypeError:
                    logger.warning(f"Cannot convert {value} for {metric_name}")
        return results

    async def get_external_track_metrics(
        self,
        track_ids: list[UUID],
        connector: str,
        metric_names: list[str],
        uow: UnitOfWorkProtocol,
        connector_instance: TrackMetadataConnector | None = None,
        progress_manager: AsyncProgressManager | None = None,
        parent_operation_id: str | None = None,
    ) -> tuple[dict[str, dict[UUID, Any]], dict[str, set[UUID]]]:
        """Get track metrics from external APIs using cache-first strategy.

        This is the primary method for retrieving track metrics. It handles multiple
        metrics efficiently by using cached values when available and only fetching
        fresh data when needed. Uses the registered connector metric configurations
        for field mapping and freshness policies.

        Args:
            track_ids: Internal track IDs to get metrics for.
            connector: External connector name ('spotify', 'lastfm', etc.).
            metric_names: List of metric names to retrieve (e.g., ['lastfm_global_playcount', 'lastfm_user_playcount']).
            uow: Unit of work for database transaction management.
            connector_instance: Optional connector instance for fresh metadata fetching.
            progress_manager: Optional progress manager for sub-operation tracking.
            parent_operation_id: Parent operation ID for sub-operation nesting.

        Returns:
            Tuple of (metrics_dict, fresh_ids_dict) where:
            - metrics_dict: Dictionary mapping metric names to track_id -> value mappings.
              Example: {'lastfm_global_playcount': {1: 85, 2: 92}, 'lastfm_user_playcount': {1: 75, 2: 120}}
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
        available_metrics = self.metric_config.get_connector_metrics(connector)
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
            field_name = self.metric_config.get_field_name(metric_name)
            if field_name:
                field_map[metric_name] = field_name
            else:
                logger.warning(f"No field mapping found for {metric_name}")

        if not field_map:
            logger.warning("No valid field mappings found for any requested metrics")
            return {}, {}

        result: dict[str, dict[UUID, Any]] = {}
        fresh_ids_per_metric: dict[str, set[UUID]] = {}

        # Phase 1: Single database transaction to identify missing data
        missing_tracks_per_metric: dict[str, list[UUID]] = {}
        cached_values_per_metric: dict[str, dict[UUID, Any]] = {}

        async with uow:
            metrics_repo = uow.get_metrics_repository()
            for metric_name in field_map:
                max_age_hours = self.metric_config.get_metric_freshness(metric_name)

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

            # Pre-load tracks that will need API calls (while UoW is still open)
            tracks_for_api: list[Track] = []
            if missing_tracks_per_metric:
                all_missing_ids: set[UUID] = set()
                for ids in missing_tracks_per_metric.values():
                    all_missing_ids.update(ids)
                track_repo = uow.get_track_repository()
                tracks_dict = await track_repo.find_tracks_by_ids(list(all_missing_ids))
                tracks_for_api = list(tracks_dict.values())

        # Phase 2: Single API fetch for all missing tracks, then extract per-metric
        if missing_tracks_per_metric and connector_instance:
            fresh_metadata: dict[UUID, dict[str, Any]] = {}

            if tracks_for_api:
                # Filter out tracks with no connector identity (avoids wasted API calls)
                tracks_with_identity = [
                    t
                    for t in tracks_for_api
                    if connector in t.connector_track_identifiers
                ]
                skipped = len(tracks_for_api) - len(tracks_with_identity)
                if skipped:
                    logger.debug(
                        f"Skipping {skipped} tracks with no {connector} identity mapping",
                        connector=connector,
                        skipped_count=skipped,
                    )

                if tracks_with_identity:
                    progress_callback = None
                    sub_op_id: str | None = None
                    try:
                        # Create sub-operation callback for granular progress
                        if progress_manager and parent_operation_id:
                            sub_op_id, progress_callback = await create_sub_operation(
                                progress_manager,
                                description=f"Fetching {connector} metadata",
                                total_items=len(tracks_with_identity),
                                parent_operation_id=parent_operation_id,
                                phase="enrich",
                                node_type="enricher",
                            )

                        fresh_metadata = (
                            await connector_instance.get_external_track_data(
                                tracks_with_identity,
                                progress_callback=progress_callback,
                            )
                        )

                        # Complete sub-operation
                        if progress_manager and sub_op_id:
                            await complete_sub_operation(progress_manager, sub_op_id)
                    except Exception as e:
                        # Complete sub-operation as failed
                        if progress_manager and sub_op_id:
                            await complete_sub_operation(
                                progress_manager,
                                sub_op_id,
                                OperationStatus.FAILED,
                            )
                        logger.error(
                            f"Failed to fetch metrics from {connector} API: {e}"
                        )
                        raise

            # Extract metrics from fresh metadata (only for missing track/metric pairs)
            missing_metric_names = [
                m for m in field_map if m in missing_tracks_per_metric
            ]
            all_extracted = self._extract_metrics_from_metadata(
                fresh_metadata, missing_metric_names, field_map, connector
            )

            # Filter to only track/metric pairs that were actually missing
            missing_sets = {
                name: set(ids) for name, ids in missing_tracks_per_metric.items()
            }
            all_metrics_to_save = [
                t for t in all_extracted if t[0] in missing_sets.get(t[2], set())
            ]

            # Update caches from extracted metrics
            for track_id, _, metric_name, converted_value in all_metrics_to_save:
                if metric_name not in cached_values_per_metric:
                    cached_values_per_metric[metric_name] = {}
                cached_values_per_metric[metric_name][track_id] = converted_value
                if metric_name not in fresh_ids_per_metric:
                    fresh_ids_per_metric[metric_name] = set()
                fresh_ids_per_metric[metric_name].add(track_id)

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
                logger.warning(f"No values retrieved for {metric_name}")

        total_values = sum(len(values) for values in result.values())
        freshly_fetched = sum(len(ids) for ids in fresh_ids_per_metric.values())
        summary = (
            f"Retrieved {len(result)} metric types with "
            f"{total_values} total values ({freshly_fetched} freshly fetched)"
        )

        if track_ids and total_values == 0:
            logger.warning(f"{summary} — downstream nodes may filter all tracks")
        else:
            logger.info(summary)

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
        ) in self.metric_config.get_all_connectors_metrics().items():
            tracks_with_metadata: list[Track] = []
            fresh_metadata: dict[UUID, dict[str, Any]] = {}

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
                    field_map=self.metric_config.get_all_field_mappings(),
                    uow=uow,
                )

    async def batch_process_fresh_metadata(
        self,
        fresh_metadata: dict[UUID, dict[str, Any]],
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

        all_metrics_batch = self._extract_metrics_from_metadata(
            fresh_metadata, available_metrics, field_map, connector
        )

        # Batch save all metrics using unified BatchProcessor
        if all_metrics_batch:
            metrics_repo = uow.get_metrics_repository()

            # Create enhanced database batch processor with progress tracking
            batch_processor = EnhancedDatabaseBatchProcessor[
                _MetricsTuple, int
            ](
                batch_size=10,  # Small batch size for incremental progress tracking
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
