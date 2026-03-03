"""Pure domain service for track match evaluation.

This service contains ALL business logic for track matching decisions with zero external dependencies.
It operates only on domain entities and algorithms, applying business rules for match acceptance.

This replaces the business logic previously scattered across:
- TrackMatchingService.evaluate_raw_provider_matches()
- Infrastructure components that were incorrectly making business decisions
"""

from typing import cast

from attrs import define
from loguru import logger as _loguru_logger

from src.domain.entities import Track
from src.domain.matching.algorithms import (
    InternalTrackData,
    ServiceTrackData,
    calculate_confidence,
)
from src.domain.matching.config import MatchingConfig
from src.domain.matching.types import MatchResult, MatchResultsById, RawProviderMatch

logger = _loguru_logger.bind(module=__name__)


@define(frozen=True, slots=True)
class TrackMatchEvaluationService:
    """Pure business logic for track matching and confidence scoring.

    This service encapsulates ALL business rules around track matching:
    - Confidence scoring algorithms via domain algorithms
    - Match acceptance thresholds (business rules)
    - Business logic for match evaluation
    - Batch processing logic

    It operates ONLY on domain entities and contains NO infrastructure concerns.
    Zero external dependencies - this is the heart of the matching domain.
    """

    config: MatchingConfig

    def _get_threshold(self, match_method: str) -> int:
        """Look up acceptance threshold for a match method."""
        thresholds = {
            "isrc": self.config.threshold_isrc,
            "mbid": self.config.threshold_mbid,
            "artist_title": self.config.threshold_artist_title,
        }
        return thresholds.get(match_method, self.config.threshold_default)

    def should_accept_match(self, confidence: int, match_method: str) -> bool:
        """Business rule for determining if a match should be accepted.

        Args:
            confidence: Calculated confidence score (0-100)
            match_method: Method used for matching ("isrc", "mbid", "artist_title")

        Returns:
            True if match meets business criteria for acceptance
        """
        return confidence >= self._get_threshold(match_method)

    def evaluate_single_match(
        self,
        track: Track,
        raw_match: RawProviderMatch,
        connector: str,
    ) -> MatchResult:
        """Evaluate a single track match using pure business logic.

        Args:
            track: Internal track entity
            raw_match: Raw data from infrastructure provider
            connector: Name of the external service connector

        Returns:
            MatchResult with confidence score and business decision
        """
        # Convert track to format expected by confidence algorithm
        internal_track_data: InternalTrackData = {
            "title": track.title,
            "artists": [artist.name for artist in track.artists]
            if track.artists
            else [],
            "duration_ms": track.duration_ms,
            "isrc": track.isrc,
        }

        # Calculate confidence using pure domain algorithm
        confidence, evidence = calculate_confidence(
            internal_track_data,
            cast(ServiceTrackData, raw_match["service_data"]),
            raw_match["match_method"],
            self.config,
        )

        # Apply business rule for match acceptance
        success = self.should_accept_match(confidence, raw_match["match_method"])

        # Create updated track with connector mapping if successful
        if success:
            updated_track = track.with_connector_track_id(
                connector, raw_match["connector_id"]
            )
        else:
            updated_track = track

        return MatchResult(
            track=updated_track,
            success=success,
            connector_id=raw_match["connector_id"],
            confidence=confidence,
            match_method=raw_match["match_method"],
            service_data=raw_match["service_data"],
            evidence=evidence,
        )

    def evaluate_raw_matches(
        self,
        tracks: list[Track],
        raw_matches: dict[int, RawProviderMatch],
        connector: str,
    ) -> MatchResultsById:
        """Evaluate raw provider matches using pure business logic.

        This is the MAIN method that applies domain business rules to raw data from
        infrastructure providers. It centralizes ALL confidence calculation and
        match acceptance logic.

        Args:
            tracks: List of internal track entities
            raw_matches: Raw match data from providers (no business logic applied)
            connector: Name of the external service connector

        Returns:
            Dictionary mapping track IDs to evaluated match results (accepted matches only)
        """
        results: MatchResultsById = {}

        for track in tracks:
            if track.id is None or track.id not in raw_matches:
                continue

            raw_match = raw_matches[track.id]

            # Apply pure business logic to raw provider data
            match_result = self.evaluate_single_match(
                track=track,
                raw_match=raw_match,
                connector=connector,
            )

            # Only include successful matches (business rule)
            if match_result.success:
                results[track.id] = match_result
            else:
                threshold = self._get_threshold(match_result.match_method)
                logger.warning(
                    f"Match rejected: '{track.title}' by '{', '.join(a.name for a in track.artists) if track.artists else 'Unknown'}' "
                    + f"(confidence {match_result.confidence} < {threshold})",
                    track_id=track.id,
                    confidence=match_result.confidence,
                    threshold=threshold,
                    match_method=match_result.match_method,
                    connector=connector,
                )

        # Log evaluation summary with contextual insights
        total_tracks = len(tracks)
        raw_matches_found = len(raw_matches)
        accepted_matches = len(results)
        rejected_matches = raw_matches_found - accepted_matches
        no_matches_found = total_tracks - raw_matches_found

        if accepted_matches > 0:
            logger.info(
                f"Match evaluation complete: {accepted_matches}/{total_tracks} tracks matched to {connector}",
                connector=connector,
                accepted=accepted_matches,
                rejected=rejected_matches,
                no_matches=no_matches_found,
                total=total_tracks,
            )

        if rejected_matches > 0:
            logger.info(
                f"Rejected {rejected_matches} matches with {connector} due to low confidence",
                connector=connector,
                rejected_count=rejected_matches,
            )

        if no_matches_found > 0:
            logger.info(
                f"{no_matches_found} tracks had no matches found in {connector}",
                connector=connector,
                no_matches_count=no_matches_found,
            )

        return results
