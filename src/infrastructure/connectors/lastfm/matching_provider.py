"""LastFM provider for track matching.

This provider handles communication with the LastFM API and transforms
LastFM track data into raw provider matches without business logic.

Satisfies the ``MatchProvider`` protocol structurally (service_name +
fetch_raw_matches_for_tracks). Does NOT inherit ``BaseMatchingProvider``
because Last.fm's batch API doesn't partition by ISRC / artist-title —
inheriting would violate LSP (the abstract template-method hooks would
raise ``NotImplementedError``).
"""

from uuid import UUID

from src.config import get_logger
from src.config.logging import logging_context
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.types import (
    MatchFailure,
    MatchFailureReason,
    ProgressCallback,
    ProviderMatchResult,
    RawProviderMatch,
)
from src.infrastructure.connectors._shared.failure_handling import (
    create_and_log_failure,
    handle_track_processing_failure,
    log_failure_summary,
)
from src.infrastructure.connectors.lastfm.connector import LastFMConnector
from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo
from src.infrastructure.connectors.lastfm.identifiers import make_lastfm_identifier

logger = get_logger(__name__)


class LastFMProvider:
    """LastFM track matching provider.

    Satisfies ``MatchProvider`` protocol structurally. Uses Last.fm's batch
    API which processes all tracks at once, so there is no ISRC / artist-title
    partitioning step.
    """

    connector_instance: LastFMConnector

    def __init__(self, connector_instance: LastFMConnector) -> None:
        """Initialize with LastFM connector.

        Args:
            connector_instance: LastFM service connector for API calls.
        """
        self.connector_instance = connector_instance

    @property
    def service_name(self) -> str:
        """Service identifier."""
        return "lastfm"

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Track],
        progress_callback: ProgressCallback | None = None,
        **additional_options: object,
    ) -> ProviderMatchResult:
        """Fetch raw track matches from LastFM.

        Args:
            tracks: Tracks to match against LastFM catalog.
            progress_callback: Optional async callback invoked with
                (completed_count, total, description) after matching completes.
            **additional_options: Additional options (unused).

        Returns:
            ProviderMatchResult with successful matches and structured failure information.
        """
        # Acknowledge additional options to satisfy linter
        _ = additional_options

        if not tracks:
            return ProviderMatchResult()

        with logging_context(operation="match_lastfm", tracks_count=len(tracks)):
            logger.info(f"Matching {len(tracks)} tracks to LastFM")

            matches: dict[UUID, RawProviderMatch] = {}
            failures: list[MatchFailure] = []

            try:
                await self._fetch_and_classify_batch(tracks, matches, failures)
            except Exception as e:
                # Batch API failed - all tracks failed
                failures.extend(
                    handle_track_processing_failure(
                        track.id, self.service_name, "batch_lookup", e
                    )
                    for track in tracks
                    if track.id
                )

            # Report progress after batch processing
            if progress_callback is not None:
                await progress_callback(
                    len(tracks),
                    len(tracks),
                    f"LastFM batch matching complete ({len(matches)} matched)",
                )

            # Log summary
            log_failure_summary(self.service_name, len(matches), len(failures))
            logger.info(f"Found {len(matches)} matches from {len(tracks)} tracks")

            return ProviderMatchResult(matches=matches, failures=failures)

    async def _fetch_and_classify_batch(
        self,
        tracks: list[Track],
        matches: dict[UUID, RawProviderMatch],
        failures: list[MatchFailure],
    ) -> None:
        """Fetch LastFM batch metadata and classify into matches/failures."""
        # Get batch track info from LastFM
        logger.info(f"Fetching LastFM metadata for {len(tracks)} tracks")

        track_infos = await self.connector_instance.get_track_info_batch(tracks=tracks)
        logger.info(
            f"LastFM API completed: retrieved {len(track_infos)} track metadata results"
        )

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
