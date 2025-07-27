"""Metric freshness controller service.

Determines when connector metrics need refreshing based on track_metrics timestamps
and configurable freshness policies.
"""

from datetime import UTC, datetime, timedelta

from src.config import get_config, get_logger
from src.domain.repositories.interfaces import ConnectorRepositoryProtocol

logger = get_logger(__name__)


class MetricFreshnessController:
    """Determines which tracks have stale metrics needing refresh.

    Uses track_metrics table timestamps and configurable freshness policies
    to identify tracks that need updated metrics from external connectors.
    """

    def __init__(self, connector_repo: ConnectorRepositoryProtocol) -> None:
        """Initialize with connector repository.

        Args:
            connector_repo: Connector repository for metrics timestamps.
        """
        self.connector_repo = connector_repo

    async def get_stale_tracks(
        self,
        track_ids: list[int],
        connector: str,
        max_age_hours: float | None = None,
    ) -> list[int]:
        """Get list of track IDs that have stale metrics needing refresh.

        Args:
            track_ids: Track IDs to check for staleness.
            connector: Connector name for freshness policy lookup.
            max_age_hours: Override freshness policy. If None, uses config default.

        Returns:
            List of track IDs that need metrics refresh.
        """
        if not track_ids:
            return []

        # Get freshness policy for this connector
        if max_age_hours is None:
            max_age_hours = self._get_freshness_policy(connector)

        # If no freshness policy defined, consider all data fresh
        if max_age_hours is None:
            logger.debug(
                f"No freshness policy for {connector}, considering all data fresh"
            )
            return []

        with logger.contextualize(
            operation="get_stale_tracks",
            connector=connector,
            max_age_hours=max_age_hours,
            track_count=len(track_ids),
        ):
            logger.info(
                f"Checking freshness for {len(track_ids)} tracks (max age: {max_age_hours}h)"
            )

            # Calculate cutoff time for freshness
            cutoff_time = datetime.now(UTC) - timedelta(hours=max_age_hours)
            logger.debug(f"Data older than {cutoff_time} considered stale")

            # Only check track metrics timestamps for this connector
            # Identity mappings are permanent - once established, they don't expire
            metrics_timestamps = await self.connector_repo.get_metadata_timestamps(
                track_ids, connector
            )

            stale_track_ids = []

            for track_id in track_ids:
                # Only use metrics timestamps to determine freshness
                # Identity mappings (track-to-connector relationships) are permanent
                metrics_timestamp = metrics_timestamps.get(track_id)

                last_updated = metrics_timestamp
                timestamp_source = "metrics" if metrics_timestamp else "none"

                # Ensure timezone consistency for metrics timestamps
                if last_updated and last_updated.tzinfo is None:
                    last_updated = last_updated.replace(tzinfo=UTC)

                if not last_updated or last_updated < cutoff_time:
                    # Metrics data is stale or missing - need to fetch fresh metrics
                    stale_track_ids.append(track_id)
                    logger.debug(
                        f"Track {track_id}: stale metrics (last_updated: {last_updated}, source: {timestamp_source})"
                    )
                else:
                    logger.debug(
                        f"Track {track_id}: fresh metrics (last_updated: {last_updated}, source: {timestamp_source})"
                    )

            logger.info(
                f"Found {len(stale_track_ids)} tracks with stale data out of {len(track_ids)} checked"
            )
            return stale_track_ids

    # _get_metrics_timestamps method removed - functionality moved to ConnectorRepository.get_metadata_timestamps()

    def _get_freshness_policy(self, connector: str) -> float | None:
        """Get freshness policy for a connector from configuration.

        Args:
            connector: Connector name.

        Returns:
            Maximum age in hours before data is considered stale, or None if no policy.
        """
        config_key = f"ENRICHER_DATA_FRESHNESS_{connector.upper()}"
        max_age_hours = get_config(config_key)

        if max_age_hours is not None:
            logger.debug(
                f"Using freshness policy for {connector}: {max_age_hours} hours"
            )
        else:
            logger.debug(f"No freshness policy configured for {connector}")

        return max_age_hours
