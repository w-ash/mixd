"""Metrics Application Service implementing Clean Architecture patterns.

This service orchestrates metric resolution, caching strategy, and persistence
following Clean Architecture principles with proper separation of concerns.
"""

from typing import Any

from attrs import define

from src.config import get_logger
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors.metrics_config import get_metric_freshness

logger = get_logger(__name__)


@define(slots=True)
class MetricsApplicationService:
    """Application service for coordinating metric operations.

    Orchestrates metric resolution with proper separation of concerns:
    - Uses domain interfaces for data access
    - Implements caching strategy as business logic
    - Coordinates metric extraction and persistence
    - Follows Clean Architecture dependency rules
    """

    async def resolve_metrics(
        self,
        track_ids: list[int],
        metric_name: str,
        connector: str,
        field_map: dict[str, str],
        uow: UnitOfWorkProtocol,
    ) -> dict[int, Any]:
        """Resolve metrics for tracks with caching strategy.

        Implements the complete metric resolution workflow:
        1. Check cached values using freshness policy
        2. Identify tracks needing fresh data
        3. Fetch missing metadata from connector repository
        4. Extract and convert metric values
        5. Persist new metrics for future use
        6. Return complete result set

        Args:
            track_ids: List of internal track IDs
            metric_name: Name of the metric to resolve
            connector: Connector name (e.g., 'spotify', 'lastfm')
            field_map: Mapping of metric names to connector fields
            uow: UnitOfWork for transaction management

        Returns:
            Dictionary mapping track IDs to their metric values
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
            if not field_name:
                logger.warning(f"No field mapping for {metric_name}")
                return cached_values

            # Step 4: Retrieve metadata for missing tracks
            connector_repo = uow.get_connector_repository()
            metadata = await connector_repo.get_connector_metadata(
                missing_ids, connector, field_name
            )

            # Step 5: Extract and convert metric values
            metrics_to_save = []
            for track_id, value in metadata.items():
                if value is not None and not isinstance(value, dict):
                    try:
                        float_value = float(value)
                        metrics_to_save.append((
                            track_id,
                            connector,
                            metric_name,
                            float_value,
                        ))
                        cached_values[track_id] = value
                    except (ValueError, TypeError):
                        logger.warning(
                            f"Cannot convert {value} to float for {metric_name}"
                        )

            # Step 6: Persist new metrics
            if metrics_to_save:
                saved_count = await metrics_repo.save_track_metrics(metrics_to_save)
                await uow.commit()
                logger.info(f"Saved {saved_count} new metrics for {metric_name}")

        return cached_values

    async def resolve_connector_metrics(
        self,
        track_id: int,
        connector: str,
        available_metrics: list[str],
        field_map: dict[str, str],
        uow: UnitOfWorkProtocol,
    ) -> dict[str, Any]:
        """Resolve all available metrics for a track from a specific connector.

        Args:
            track_id: The track ID to resolve metrics for
            connector: The connector name
            available_metrics: List of metrics this connector supports
            field_map: Field mapping for metric extraction
            uow: UnitOfWork for transaction management

        Returns:
            Dictionary of resolved metrics {metric_name: value}
        """
        if not available_metrics:
            return {}

        logger.info(
            f"Resolving all {connector} metrics for track {track_id}",
            connector=connector,
            track_id=track_id,
            metrics=available_metrics,
        )

        async with uow:
            # Get metadata using the same transaction
            connector_repo = uow.get_connector_repository()
            metadata = await connector_repo.get_connector_metadata(
                [track_id], connector
            )

            if not metadata or track_id not in metadata:
                logger.debug(f"No metadata found for track {track_id} from {connector}")
                return {}

            # Extract all available metrics
            results = {}
            metrics_to_save = []

            track_metadata = metadata[track_id]
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
                    metrics_to_save.append((
                        track_id,
                        connector,
                        metric_name,
                        float_value,
                    ))
                    results[metric_name] = float_value
                except (ValueError, TypeError):
                    logger.warning(f"Cannot convert {value} to float for {metric_name}")

            # Persist all metrics in a single operation
            if metrics_to_save:
                metrics_repo = uow.get_metrics_repository()
                await metrics_repo.save_track_metrics(metrics_to_save)
                await uow.commit()

                logger.info(
                    f"Saved {len(metrics_to_save)} metrics for track {track_id}",
                    connector=connector,
                    metrics=[m[2] for m in metrics_to_save],
                )

        return results

    async def batch_process_fresh_metadata(
        self,
        fresh_metadata: dict[int, dict[str, Any]],
        connector: str,
        available_metrics: list[str],
        field_map: dict[str, str],
        uow: UnitOfWorkProtocol,
    ) -> int:
        """Batch process metrics from fresh metadata within existing transaction.

        Optimized for processing large amounts of fresh metadata efficiently
        within the calling service's transaction context.

        Args:
            fresh_metadata: Dictionary of track_id -> metadata
            connector: Connector name
            available_metrics: List of metrics this connector supports
            field_map: Field mapping for metric extraction
            uow: UnitOfWork for transaction management

        Returns:
            Number of metrics processed
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

        # Batch save all metrics at once
        if all_metrics_batch:
            metrics_repo = uow.get_metrics_repository()

            # Use smaller batch sizes to prevent SQLite locks with large datasets
            max_batch_size = 10
            saved_count = 0
            for i in range(0, len(all_metrics_batch), max_batch_size):
                batch_slice = all_metrics_batch[i : i + max_batch_size]
                batch_saved = await metrics_repo.save_track_metrics(batch_slice)
                saved_count += batch_saved

            logger.info(
                f"Batch saved {saved_count} metrics for {len(fresh_metadata)} tracks",
                connector=connector,
            )
            return saved_count

        return 0
