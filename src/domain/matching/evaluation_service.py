"""Pure domain service for track match evaluation.

This service contains ALL business logic for track matching decisions with zero external dependencies.
It operates only on domain entities and algorithms, applying business rules for match acceptance.

This replaces the business logic previously scattered across:
- TrackMatchingService.evaluate_raw_provider_matches()
- Infrastructure components that were incorrectly making business decisions
"""

from typing import ClassVar

from src.domain.entities import Track
from src.domain.matching.algorithms import calculate_confidence
from src.domain.matching.types import MatchResult, MatchResultsById, RawProviderMatch


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

    # Business rule: confidence thresholds for match acceptance
    CONFIDENCE_THRESHOLDS: ClassVar[dict[str, int]] = {
        "isrc": 85,  # High confidence for ISRC matches
        "mbid": 85,  # High confidence for MusicBrainz ID matches
        "artist_title": 70,  # Lower threshold for fuzzy matches
        "default": 60,  # Minimum acceptable confidence
    }

    def should_accept_match(self, confidence: int, match_method: str) -> bool:
        """Business rule for determining if a match should be accepted.

        Args:
            confidence: Calculated confidence score (0-100)
            match_method: Method used for matching ("isrc", "mbid", "artist_title")

        Returns:
            True if match meets business criteria for acceptance
        """
        threshold = self.CONFIDENCE_THRESHOLDS.get(
            match_method, self.CONFIDENCE_THRESHOLDS["default"]
        )
        return confidence >= threshold

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
        internal_track_data = {
            "title": track.title,
            "artists": [artist.name for artist in track.artists]
            if track.artists
            else [],
            "duration_ms": track.duration_ms,
            "isrc": track.isrc,
        }

        # Calculate confidence using pure domain algorithm
        confidence, evidence = calculate_confidence(
            internal_track_data, raw_match["service_data"], raw_match["match_method"]
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
        results = {}

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
            # Note: Rejected matches are silently filtered out per business rule
            # Logging of rejections should be handled by application layer if needed

        return results

    def calculate_match_confidence_threshold(self, connector: str) -> int:
        """Get confidence threshold for a specific connector.

        Business rule that may vary by connector based on data quality expectations.

        Args:
            connector: Name of external service

        Returns:
            Minimum confidence threshold for accepting matches
        """
        # Business rule: some connectors have different quality expectations
        connector_thresholds = {
            "spotify": 75,  # Higher threshold for Spotify (good metadata)
            "lastfm": 65,  # Lower threshold for Last.fm (variable quality)
            "musicbrainz": 80,  # High threshold for MusicBrainz (canonical data)
        }

        return connector_thresholds.get(
            connector, self.CONFIDENCE_THRESHOLDS["default"]
        )

    def get_supported_match_methods(self) -> list[str]:
        """Get list of supported match methods with business priority order.

        Returns:
            List of match methods in order of business preference (highest confidence first)
        """
        return ["isrc", "mbid", "artist_title"]

    def get_match_method_priority(self, match_method: str) -> int:
        """Get business priority score for a match method.

        Used when multiple match methods are available to choose the best one.

        Args:
            match_method: Method used for matching

        Returns:
            Priority score (higher is better)
        """
        priorities = {
            "isrc": 100,  # Highest priority - ISRC is authoritative
            "mbid": 90,  # High priority - MusicBrainz is reliable
            "artist_title": 70,  # Lower priority - fuzzy matching
        }
        return priorities.get(match_method, 0)
