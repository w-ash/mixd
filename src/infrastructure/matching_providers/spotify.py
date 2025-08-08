"""Spotify provider for track matching.

This provider handles communication with the Spotify API and transforms
Spotify track data into our domain MatchResult objects.
"""

from typing import Any

from src.config import get_logger, settings
from src.domain.entities import Track
from src.domain.matching.types import (
    MatchFailureReason,
    ProviderMatchResult,
    RawProviderMatch,
)
from src.infrastructure.connectors.api_batch_processor import APIBatchProcessor
from src.infrastructure.matching_providers.failure_logging import log_failure_summary
from src.infrastructure.matching_providers.failure_utils import (
    create_and_log_failure,
    handle_track_processing_failure,
    merge_results,
    validate_track_for_method,
)

logger = get_logger(__name__)


class SpotifyProvider:
    """Spotify track matching provider."""

    def __init__(self, connector_instance: Any) -> None:
        """Initialize with Spotify connector.

        Args:
            connector_instance: Spotify service connector for API calls.
        """
        self.connector_instance = connector_instance
        
        # Create APIBatchProcessor with Spotify settings
        self._batch_processor = APIBatchProcessor[Track, dict[int, RawProviderMatch]](
            batch_size=settings.api.spotify_batch_size,
            concurrency_limit=settings.api.spotify_concurrency,
            retry_count=settings.api.spotify_retry_count,
            retry_base_delay=settings.api.spotify_retry_base_delay,
            retry_max_delay=settings.api.spotify_retry_max_delay,
            request_delay=settings.api.spotify_request_delay,
            logger_instance=logger,
        )

    @property
    def service_name(self) -> str:
        """Service identifier."""
        return "spotify"

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Track],
        **additional_options: Any,
    ) -> ProviderMatchResult:
        """Fetch raw track matches from Spotify using ISRC and search APIs.

        Prioritizes ISRC matches for higher confidence, then falls back to
        artist/title search for remaining tracks.

        Args:
            tracks: Tracks to match against Spotify catalog.
            **additional_options: Additional options (unused).

        Returns:
            ProviderMatchResult with successful matches and structured failure information.
        """
        # Acknowledge additional options to satisfy linter
        _ = additional_options

        if not tracks:
            return ProviderMatchResult()

        with logger.contextualize(operation="match_spotify", track_count=len(tracks)):
            # Group tracks by matching method for processing efficiency
            isrc_tracks = [t for t in tracks if t.isrc]
            artist_title_tracks = [t for t in tracks if not t.isrc and t.artists and t.title]
            
            # Handle unprocessable tracks
            unprocessable_failures = [
                create_and_log_failure(
                    track_id=t.id,
                    reason=MatchFailureReason.NO_METADATA,
                    service=self.service_name,
                    method="unknown",
                    details="Track missing artist or title data",
                )
                for t in tracks 
                if t.id and not t.isrc and (not t.artists or not t.title)
            ]

            # Process tracks by method and merge results
            isrc_result = await self._process_tracks_by_method(isrc_tracks, "isrc") if isrc_tracks else ProviderMatchResult()
            remaining_tracks = [t for t in artist_title_tracks if t.id not in isrc_result.matches]
            artist_result = await self._process_tracks_by_method(remaining_tracks, "artist_title") if remaining_tracks else ProviderMatchResult()
            
            # Merge all results
            final_result = merge_results(
                isrc_result,
                artist_result,
                ProviderMatchResult(failures=unprocessable_failures)
            )
            
            # Log summary
            log_failure_summary(self.service_name, len(final_result.matches), len(final_result.failures))
            logger.info(f"Found {len(final_result.matches)} matches from {len(tracks)} tracks")
            
            return final_result

    async def _process_tracks_by_method(self, tracks: list[Track], method: str) -> ProviderMatchResult:
        """Process tracks using specified method with structured failure handling."""
        logger.info(f"Processing {len(tracks)} tracks with {method}")
        
        matches = {}
        failures = []
        
        # Define method-specific validators and connectors
        validators = {
            "isrc": lambda t: bool(t.isrc),
            "artist_title": lambda t: bool(t.artists and t.title),
        }
        
        api_calls = {
            "isrc": lambda t: self.connector_instance.search_by_isrc(t.isrc),
            "artist_title": lambda t: self.connector_instance.search_track(
                t.artists[0].name if t.artists else "", t.title
            ),
        }
        
        failure_reasons = {
            "isrc": MatchFailureReason.NO_ISRC,
            "artist_title": MatchFailureReason.NO_METADATA,
        }
        
        failure_messages = {
            "isrc": "Track missing ISRC code",
            "artist_title": "Track missing artist or title data",
        }
        
        for track in tracks:
            if not track.id:
                continue
                
            # Validate track for method
            validation_failure = validate_track_for_method(
                track, method, self.service_name, validators[method],
                failure_reasons[method], failure_messages[method]
            )
            if validation_failure:
                failures.append(validation_failure)
                continue
                
            # Attempt API call
            try:
                result = await api_calls[method](track)
                if result and result.get("id"):
                    raw_match = self._create_raw_match(result, method)
                    if raw_match:
                        matches[track.id] = raw_match
                    else:
                        failures.append(create_and_log_failure(
                            track.id, MatchFailureReason.INVALID_RESPONSE, self.service_name,
                            method, "Failed to create raw match from Spotify response"
                        ))
                else:
                    details = f"No Spotify results for ISRC: {track.isrc}" if method == "isrc" else \
                             f"No Spotify results for '{track.artists[0].name if track.artists else ''} - {track.title}'"
                    failures.append(create_and_log_failure(
                        track.id, MatchFailureReason.NO_RESULTS, self.service_name, method, details
                    ))
                    
            except Exception as e:
                failures.append(handle_track_processing_failure(track.id, self.service_name, method, e))
                
        return ProviderMatchResult(matches=matches, failures=failures)

    def _create_raw_match(
        self, spotify_track: dict[str, Any], match_method: str
    ) -> RawProviderMatch | None:
        """Create raw match data from Spotify track data.

        This method extracts and formats data from Spotify API without applying
        any business logic, confidence scoring, or match decisions.

        Args:
            spotify_track: Spotify API response.
            match_method: Match method used ("isrc" or "artist_title").

        Returns:
            Raw provider match data, or None if creation fails.
        """
        try:
            spotify_id = spotify_track["id"]

            # Extract service data without any business logic
            service_data = {
                "title": spotify_track.get("name"),
                "album": spotify_track.get("album", {}).get("name"),
                "artists": [
                    artist.get("name", "")
                    for artist in spotify_track.get("artists", [])
                ],
                "duration_ms": spotify_track.get("duration_ms"),
                "release_date": spotify_track.get("album", {}).get("release_date"),
                "popularity": spotify_track.get("popularity"),
                "isrc": spotify_track.get("external_ids", {}).get("isrc"),
            }

            # Return raw data - no confidence calculation or business logic
            return RawProviderMatch(
                connector_id=spotify_id,
                match_method=match_method,
                service_data=service_data,
            )

        except Exception as e:
            logger.warning(f"Failed to create Spotify raw match: {e}")
            return None
