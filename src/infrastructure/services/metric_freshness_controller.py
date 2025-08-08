"""Prevents unnecessary API calls by checking if track metadata is still fresh.

Determines which tracks need updated metrics from external services (Last.fm, Spotify, etc.)
by comparing last-updated timestamps against configurable age limits.
"""

from datetime import UTC, datetime, timedelta

from src.config import get_logger, settings
from src.domain.repositories.interfaces import ConnectorRepositoryProtocol

logger = get_logger(__name__)


class MetricFreshnessController:
    """Identifies tracks needing fresh metadata to avoid redundant API calls.

    Compares track metadata timestamps against configurable age limits to determine
    which tracks need updated play counts, popularity scores, or other metrics.
    """

    def __init__(self, connector_repo: ConnectorRepositoryProtocol) -> None:
        """Initialize with repository for accessing metadata timestamps.

        Args:
            connector_repo: Repository for querying track metadata timestamps.
        """
        self.connector_repo = connector_repo

    async def get_stale_tracks(
        self,
        track_ids: list[int],
        connector: str,
        max_age_hours: float | None = None,
    ) -> list[int]:
        """Returns track IDs whose metadata is older than the freshness limit.

        Checks when each track's metadata was last updated from the specified service
        and returns those exceeding the age threshold to trigger fresh API calls.

        Args:
            track_ids: Track IDs to check for stale metadata.
            connector: Service name (e.g., 'lastfm', 'spotify') for age limit lookup.
            max_age_hours: Override default age limit. If None, uses config value.

        Returns:
            Track IDs needing fresh metadata from the connector service.
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
        """Retrieves age limit configuration for a connector service.

        Args:
            connector: Service name to look up age limit for.

        Returns:
            Maximum age in hours before metadata is considered stale, or None if unset.
        """
        if connector.lower() == "lastfm":
            max_age_hours = settings.freshness.lastfm_hours
        elif connector.lower() == "spotify":
            max_age_hours = settings.freshness.spotify_hours
        elif connector.lower() == "musicbrainz":
            max_age_hours = settings.freshness.musicbrainz_hours
        else:
            max_age_hours = None

        if max_age_hours is not None:
            logger.debug(
                f"Using freshness policy for {connector}: {max_age_hours} hours"
            )
        else:
            logger.debug(f"No freshness policy configured for {connector}")

        return max_age_hours
