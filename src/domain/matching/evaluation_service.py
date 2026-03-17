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
from src.domain.matching.types import (
    EvaluationResult,
    MatchResult,
    MatchResultsById,
    RawProviderMatch,
)

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

    def should_accept_match(self, confidence: int, match_method: str) -> bool:
        """Business rule: auto-accept if above the upper threshold.

        Args:
            confidence: Calculated confidence score (0-100).
            match_method: Method used for matching ("isrc", "mbid", "artist_title").

        Returns:
            True if match should be auto-accepted.
        """
        return confidence >= self.config.auto_accept_threshold

    def should_review_match(self, confidence: int, match_method: str) -> bool:
        """Business rule: queue for review if in the gray zone.

        The gray zone is between review_threshold and auto_accept_threshold.
        Matches below review_threshold are auto-rejected.

        Args:
            confidence: Calculated confidence score (0-100).
            match_method: Method used for matching ("isrc", "mbid", "artist_title").

        Returns:
            True if match should be queued for human review.
        """
        return (
            self.config.review_threshold
            <= confidence
            < self.config.auto_accept_threshold
        )

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

        # Apply three-zone classification
        match_method = raw_match["match_method"]
        success = self.should_accept_match(confidence, match_method)
        review_required = not success and self.should_review_match(
            confidence, match_method
        )

        # Create updated track with connector mapping if auto-accepted
        if success:
            updated_track = track.with_connector_track_id(
                connector, raw_match["connector_id"]
            )
        else:
            updated_track = track

        return MatchResult(
            track=updated_track,
            success=success,
            review_required=review_required,
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
    ) -> EvaluationResult:
        """Evaluate raw provider matches using three-zone classification.

        Classifies each match into one of three zones:
        - **Auto-accept**: confidence >= auto_accept_threshold → accepted immediately
        - **Review**: review_threshold <= confidence < auto_accept_threshold → queued for human review
        - **Auto-reject**: confidence < review_threshold → silently discarded

        Args:
            tracks: List of internal track entities.
            raw_matches: Raw match data from providers (no business logic applied).
            connector: Name of the external service connector.

        Returns:
            EvaluationResult with accepted matches and review candidates.
        """
        accepted: MatchResultsById = {}
        review_candidates: MatchResultsById = {}

        for track in tracks:
            if track.id is None or track.id not in raw_matches:
                continue

            raw_match = raw_matches[track.id]

            match_result = self.evaluate_single_match(
                track=track,
                raw_match=raw_match,
                connector=connector,
            )

            if match_result.success:
                accepted[track.id] = match_result
            elif match_result.review_required:
                review_candidates[track.id] = match_result
                logger.info(
                    f"Match queued for review: '{track.title}' "
                    + f"(confidence {match_result.confidence}, "
                    + f"review zone {self.config.review_threshold}-{self.config.auto_accept_threshold})",
                    track_id=track.id,
                    confidence=match_result.confidence,
                    match_method=match_result.match_method,
                    connector=connector,
                )
            else:
                logger.warning(
                    f"Match rejected: '{track.title}' by '{', '.join(a.name for a in track.artists) if track.artists else 'Unknown'}' "
                    + f"(confidence {match_result.confidence} < {self.config.review_threshold})",
                    track_id=track.id,
                    confidence=match_result.confidence,
                    threshold=self.config.review_threshold,
                    match_method=match_result.match_method,
                    connector=connector,
                )

        # Log evaluation summary
        total_tracks = len(tracks)
        raw_matches_found = len(raw_matches)
        no_matches_found = total_tracks - raw_matches_found
        rejected = raw_matches_found - len(accepted) - len(review_candidates)

        if len(accepted) > 0 or len(review_candidates) > 0:
            logger.info(
                f"Match evaluation: {len(accepted)} accepted, {len(review_candidates)} for review, "
                + f"{rejected} rejected, {no_matches_found} not found ({connector})",
                connector=connector,
                accepted=len(accepted),
                review=len(review_candidates),
                rejected=rejected,
                no_matches=no_matches_found,
                total=total_tracks,
            )

        return EvaluationResult(
            accepted=accepted,
            review_candidates=review_candidates,
        )
