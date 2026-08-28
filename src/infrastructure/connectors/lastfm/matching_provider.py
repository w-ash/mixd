"""LastFM provider for track matching.

This provider handles communication with the LastFM API and transforms
LastFM track data into raw provider matches without business logic.

Last.fm's batch API answers every track at once, so the provider runs the
shared workflow shell with the ``SingleBatch`` strategy — no ISRC /
artist-title partitioning step.
"""

from typing import override
from uuid import UUID

from src.config import get_logger
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.types import (
    MatchFailure,
    MatchFailureReason,
    RawProviderMatch,
)
from src.infrastructure.connectors._shared.failure_handling import (
    create_and_log_failure,
)
from src.infrastructure.connectors._shared.matching_provider import (
    BaseMatchingProvider,
    MatchStrategy,
    SingleBatch,
)
from src.infrastructure.connectors.lastfm.connector import LastFMConnector
from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo
from src.infrastructure.connectors.lastfm.identifiers import make_lastfm_identifier

logger = get_logger(__name__)


class LastFMProvider(BaseMatchingProvider):
    """LastFM track matching provider.

    Uses Last.fm's batch API which processes all tracks at once, so the
    match strategy is ``SingleBatch``.
    """

    connector_instance: LastFMConnector

    def __init__(self, connector_instance: LastFMConnector) -> None:
        """Initialize with LastFM connector.

        Args:
            connector_instance: LastFM service connector for API calls.
        """
        self.connector_instance = connector_instance

    @property
    @override
    def service_name(self) -> str:
        """Service identifier."""
        return "lastfm"

    @override
    def _match_strategy(self) -> MatchStrategy:
        """One batch call over the whole track list."""
        return SingleBatch(match_batch=self._match_batch, label="LastFM")

    async def _match_batch(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Fetch LastFM batch metadata and classify into matches/failures."""
        # Get batch track info from LastFM
        logger.info(f"Fetching LastFM metadata for {len(tracks)} tracks")

        track_infos = await self.connector_instance.get_track_info_batch(tracks=tracks)
        logger.info(
            f"LastFM API completed: retrieved {len(track_infos)} track metadata results"
        )

        matches: dict[UUID, RawProviderMatch] = {}
        failures: list[MatchFailure] = []

        # Process results and classify failures
        processed_track_ids: set[UUID] = set()
        for track_id, track_info in track_infos.items():
            processed_track_ids.add(track_id)

            if track_info and track_info.lastfm_url:
                raw_match = self._create_raw_match(track_info)
                if raw_match:
                    matches[track_id] = raw_match
                else:
                    failures.append(
                        create_and_log_failure(
                            track_id,
                            MatchFailureReason.INVALID_RESPONSE,
                            self.service_name,
                            "batch_lookup",
                            "Failed to create raw match from LastFM response",
                        )
                    )
            else:
                failures.append(
                    create_and_log_failure(
                        track_id,
                        MatchFailureReason.NO_RESULTS,
                        self.service_name,
                        "batch_lookup",
                        "No LastFM data available for track",
                    )
                )

        # Handle tracks that weren't returned by LastFM API
        failures.extend(
            create_and_log_failure(
                track.id,
                MatchFailureReason.NO_RESULTS,
                self.service_name,
                "batch_lookup",
                "Track not found in LastFM batch response",
            )
            for track in tracks
            if track.id and track.id not in processed_track_ids
        )

        return matches, failures

    def _create_raw_match(self, track_info: LastFMTrackInfo) -> RawProviderMatch | None:
        """Create raw match data from LastFM track data.

        This method extracts and formats data from LastFM API without applying
        any business logic, confidence scoring, or match decisions.

        Args:
            track_info: Typed LastFMTrackInfo from Last.fm operations.

        Returns:
            Raw provider match data, or None if creation fails.
        """
        try:
            # Return raw data - no confidence calculation or business logic.
            # Gated first: without a URL, Last.fm has no page to preserve as
            # provenance, so there is nothing worth matching on either.
            if not track_info.lastfm_url:
                return None

            # Extract service data without any business logic. The URL is
            # kept only as provenance here — it is no longer the connector_id
            # (that's now the normalized artist::title composite, the single
            # scheme shared by every Last.fm mint site).
            service_data: dict[str, JsonValue] = {
                "title": track_info.lastfm_title,
                "artist": track_info.lastfm_artist_name,
                "artists": [track_info.lastfm_artist_name]
                if track_info.lastfm_artist_name
                else [],
                "duration_ms": track_info.lastfm_duration,
                "lastfm_url": track_info.lastfm_url,
                # LastFM specific data
                "lastfm_user_playcount": track_info.lastfm_user_playcount,
                "lastfm_global_playcount": track_info.lastfm_global_playcount,
                "lastfm_listeners": track_info.lastfm_listeners,
                "lastfm_user_loved": track_info.lastfm_user_loved,
            }
            # Preserve the MBID as provenance only — Last.fm MBIDs are
            # type-confused and merge-stale ("never trust any MBIDs from the
            # Last.fm API" — MetaBrainz), so they must not earn ISRC-grade
            # "mbid" scoring. A future path may verify against MusicBrainz
            # (Track-vs-Recording + 301-merge aware) to re-earn it.
            if track_info.lastfm_mbid:
                service_data["lastfm_mbid"] = track_info.lastfm_mbid

            connector_id = make_lastfm_identifier(
                track_info.lastfm_artist_name or "", track_info.lastfm_title or ""
            )

            return RawProviderMatch(
                connector_id=connector_id,
                match_method="artist_title",
                service_data=service_data,
            )

        except Exception as e:
            logger.warning(f"Failed to create LastFM raw match: {e}")
            return None
