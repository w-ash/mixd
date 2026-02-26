"""Base class for track matching providers using Template Method pattern.

This module provides workflow orchestration for matching providers WITHOUT
business logic. All business decisions (confidence, thresholds, acceptance)
remain in the domain layer.
"""

from abc import ABC, abstractmethod
from typing import Any

from src.config import get_logger
from src.domain.entities import Track
from src.domain.matching.types import (
    MatchFailure,
    MatchFailureReason,
    ProviderMatchResult,
    RawProviderMatch,
)
from src.infrastructure.connectors._shared.failure_handling import (
    create_and_log_failure,
    log_failure_summary,
)

logger = get_logger(__name__)


class BaseMatchingProvider(ABC):
    """Base class for matching providers - TECHNICAL concerns only.

    This class contains workflow orchestration and technical utilities.
    NO business logic (confidence, thresholds, acceptance decisions).

    Architecture compliance:
    - Infrastructure layer: Data extraction and API communication
    - Domain layer: Business logic (confidence, thresholds, evaluation)
    - Application layer: Orchestration of infrastructure → domain flow

    Subclasses must implement:
    - service_name: Service identifier property
    - _match_by_isrc(): Service-specific ISRC matching
    - _match_by_artist_title(): Service-specific artist/title matching
    """

    @property
    @abstractmethod
    def service_name(self) -> str:
        """Service identifier for logging and failure tracking."""
        ...

    @abstractmethod
    async def _match_by_isrc(
        self, tracks: list[Track]
    ) -> tuple[dict[int, RawProviderMatch], list[MatchFailure]]:
        """Service-specific ISRC matching.

        Args:
            tracks: Tracks with ISRC to match.

        Returns:
            Tuple of (matches dict, failures list).
        """
        ...

    @abstractmethod
    async def _match_by_artist_title(
        self, tracks: list[Track]
    ) -> tuple[dict[int, RawProviderMatch], list[MatchFailure]]:
        """Service-specific artist/title matching.

        Args:
            tracks: Tracks with artist and title to match.

        Returns:
            Tuple of (matches dict, failures list).
        """
        ...

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Track],
        **additional_options: Any,
    ) -> ProviderMatchResult:
        """Orchestrate matching workflow using template method pattern.

        This method coordinates the matching process:
        1. Partition tracks by method (ISRC vs artist/title vs unprocessable)
        2. Call service-specific matching methods
        3. Filter already-matched tracks from fallback method
        4. Merge all results
        5. Log summary

        Args:
            tracks: Tracks to match against external service.
            **additional_options: Additional options (acknowledged but unused).

        Returns:
            ProviderMatchResult with matches and structured failures.
        """
        # Acknowledge additional options to satisfy linter
        _ = additional_options

        if not tracks:
            return ProviderMatchResult()

        with logger.contextualize(
            operation=f"match_{self.service_name}", track_count=len(tracks)
        ):
            # Partition tracks by matching method
            isrc_tracks, artist_title_tracks, unprocessable_tracks = (
                self._partition_tracks(tracks)
            )

            # Create failures for unprocessable tracks
            unprocessable_failures = [
                create_and_log_failure(
                    track_id=t.id,
                    reason=MatchFailureReason.NO_METADATA,
                    service=self.service_name,
                    method="unknown",
                    details="Track missing artist or title data",
                )
                for t in unprocessable_tracks
                if t.id
            ]

            # Process ISRC tracks
            isrc_matches: dict[int, RawProviderMatch] = {}
            isrc_failures: list[MatchFailure] = []
            if isrc_tracks:
                isrc_matches, isrc_failures = await self._match_by_isrc(isrc_tracks)

            # Filter out tracks already matched by ISRC from artist/title candidates
            remaining_tracks = [
                t for t in artist_title_tracks if t.id not in isrc_matches
            ]

            # Process remaining tracks by artist/title
            artist_title_matches: dict[int, RawProviderMatch] = {}
            artist_title_failures: list[MatchFailure] = []
            if remaining_tracks:
                (
                    artist_title_matches,
                    artist_title_failures,
                ) = await self._match_by_artist_title(remaining_tracks)

            # Merge all results
            all_matches = {**isrc_matches, **artist_title_matches}
            all_failures = (
                isrc_failures + artist_title_failures + unprocessable_failures
            )

            final_result = ProviderMatchResult(
                matches=all_matches, failures=all_failures
            )

            # Log summary
            log_failure_summary(
                self.service_name, len(final_result.matches), len(final_result.failures)
            )
            logger.info(
                f"Found {len(final_result.matches)} matches from {len(tracks)} tracks"
            )

            return final_result

    def _partition_tracks(
        self, tracks: list[Track]
    ) -> tuple[list[Track], list[Track], list[Track]]:
        """Partition tracks by matching method.

        Args:
            tracks: All tracks to partition.

        Returns:
            Tuple of (isrc_tracks, artist_title_tracks, unprocessable_tracks).
        """
        isrc_tracks: list[Track] = []
        artist_title_tracks: list[Track] = []
        unprocessable_tracks: list[Track] = []

        for track in tracks:
            if self._has_isrc(track):
                # ISRC takes priority
                isrc_tracks.append(track)
            elif self._has_artist_and_title(track):
                # Fallback to artist/title
                artist_title_tracks.append(track)
            else:
                # Cannot process
                unprocessable_tracks.append(track)

        return isrc_tracks, artist_title_tracks, unprocessable_tracks

    def _has_isrc(self, track: Track) -> bool:
        """Check if track has ISRC for matching.

        Args:
            track: Track to validate.

        Returns:
            True if track has ISRC.
        """
        return bool(track.isrc)

    def _has_artist_and_title(self, track: Track) -> bool:
        """Check if track has artist and title for matching.

        Args:
            track: Track to validate.

        Returns:
            True if track has both artist and title.
        """
        return bool(track.artists and track.title)
