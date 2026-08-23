"""Base class for track matching providers using Template Method pattern.

This module provides workflow orchestration for matching providers WITHOUT
business logic. All business decisions (confidence, thresholds, acceptance)
remain in the domain layer.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import ClassVar
from uuid import UUID

from src.config import get_logger
from src.config.logging import logging_context
from src.domain.entities import Track
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

logger = get_logger(__name__)


class BaseMatchingProvider(ABC):
    """Base class for matching providers - TECHNICAL concerns only.

    This class contains workflow orchestration and technical utilities.
    NO business logic (confidence, thresholds, acceptance decisions).

    Architecture compliance:
    - Infrastructure layer: Data extraction and API communication
    - Domain layer: Business logic (confidence, thresholds, evaluation)
    - Application layer: Orchestration of infrastructure → domain flow

    Validation happens once, in ``_partition_tracks`` (the single validation
    point): tracks handed to ``_match_by_isrc`` are guaranteed to carry an
    ISRC and tracks handed to ``_match_by_artist_title`` a valid artist/title,
    so subclass hooks must not re-validate.

    Subclasses must implement:
    - service_name: Service identifier property
    - _match_by_isrc(): Service-specific ISRC matching
    - _match_by_artist_title(): Service-specific artist/title matching —
      unless ``supports_artist_title_matching`` is False, in which case the
      base default (raising NotImplementedError) is never reached.
    """

    # Does this provider match by artist/title at all? Conservative providers
    # (Apple Music) set this False: tracks without an ISRC fail with NO_ISRC
    # instead of entering the artist/title partition, ISRC misses are not
    # funneled into a second-chance search, and ``_match_by_artist_title`` is
    # never called. Failures for ISRC *misses* stay with ``_match_by_isrc`` —
    # the hook already reports its own misses, and a second base-level failure
    # for the same track would double-count it.
    supports_artist_title_matching: ClassVar[bool] = True

    @property
    @abstractmethod
    def service_name(self) -> str:
        """Service identifier for logging and failure tracking."""
        ...

    @abstractmethod
    async def _match_by_isrc(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Service-specific ISRC matching.

        Args:
            tracks: Tracks with ISRC to match.

        Returns:
            Tuple of (matches dict, failures list).
        """
        ...

    async def _match_by_artist_title(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Service-specific artist/title matching.

        Default: unimplemented. The flag contract — providers that set
        ``supports_artist_title_matching = False`` (Apple Music, Tidal)
        never have this hook called: ISRC-less tracks fail via
        ``_isrc_only_skip_failures`` instead. The hook stays as the v0.12.1
        alias-aware-comparator plug point: when that lands, a provider
        implements this with catalog search + alias-aware evaluation and
        flips its class flag to True.

        Args:
            tracks: Tracks with artist and title to match.

        Returns:
            Tuple of (matches dict, failures list).
        """
        raise NotImplementedError(
            f"{self.service_name} does not implement artist/title matching — "
            "it lands with the v0.12.1 alias-aware comparator"
        )

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Track],
        progress_callback: ProgressCallback | None = None,
        **additional_options: object,
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
            progress_callback: Optional async callback invoked with
                (completed_count, total, description) after each matching phase.
            **additional_options: Additional options (acknowledged but unused).

        Returns:
            ProviderMatchResult with matches and structured failures.
        """
        # Acknowledge additional options to satisfy linter
        _ = additional_options

        if not tracks:
            return ProviderMatchResult()

        total = len(tracks)

        with logging_context(operation=f"match_{self.service_name}", track_count=total):
            # Partition tracks by matching method
            isrc_tracks, artist_title_tracks, unprocessable_tracks = (
                self._partition_tracks(tracks)
            )

            # Create failures for unprocessable tracks. Id-less tracks emit a
            # failure too (track_id=None): they cannot be addressed per-track
            # but must not vanish — same doctrine as the ISRC-only skip path.
            unprocessable_failures = [
                create_and_log_failure(
                    track_id=t.id,
                    reason=MatchFailureReason.NO_METADATA,
                    service=self.service_name,
                    method="unknown",
                    details="Track missing artist or title data"
                    if t.id
                    else "Track has no database id and no usable metadata",
                )
                for t in unprocessable_tracks
            ]

            completed = len(unprocessable_tracks)

            # Process ISRC tracks
            isrc_matches: dict[UUID, RawProviderMatch] = {}
            isrc_failures: list[MatchFailure] = []
            if isrc_tracks:
                isrc_matches, isrc_failures = await self._match_by_isrc(isrc_tracks)
                completed += len(isrc_tracks)
                if progress_callback is not None:
                    await progress_callback(
                        completed,
                        total,
                        f"ISRC matching complete ({len(isrc_matches)} matched)",
                    )

            # ISRC-only providers never reach artist/title: ISRC-less tracks
            # fail as NO_ISRC here (id-less ones as NO_METADATA — they cannot
            # be addressed per-track but must not vanish), ISRC misses keep
            # the failure their _match_by_isrc already reported, and no
            # fallback list is built.
            no_isrc_failures: list[MatchFailure] = []
            if not self.supports_artist_title_matching:
                no_isrc_failures = self._isrc_only_skip_failures(artist_title_tracks)
                completed += len(artist_title_tracks)
                if progress_callback is not None and artist_title_tracks:
                    await progress_callback(
                        completed,
                        total,
                        f"Skipped {len(artist_title_tracks)} tracks without "
                        "ISRC (ISRC-only provider)",
                    )
                remaining_tracks: list[Track] = []
            else:
                # Fallback: failed ISRC tracks with valid artist/title get a second chance
                failed_isrc_tracks = [
                    t
                    for t in isrc_tracks
                    if t.id not in isrc_matches and self._has_artist_and_title(t)
                ]
                if failed_isrc_tracks:
                    logger.info(
                        f"Falling back to artist/title for {len(failed_isrc_tracks)} failed ISRC tracks"
                    )

                # Filter out tracks already matched by ISRC, then add failed ISRC fallbacks
                remaining_tracks = [
                    t for t in artist_title_tracks if t.id not in isrc_matches
                ] + failed_isrc_tracks

            # Process remaining tracks by artist/title
            artist_title_matches: dict[UUID, RawProviderMatch] = {}
            artist_title_failures: list[MatchFailure] = []
            if remaining_tracks:
                (
                    artist_title_matches,
                    artist_title_failures,
                ) = await self._match_by_artist_title(remaining_tracks)
                completed += len(remaining_tracks)
                if progress_callback is not None:
                    await progress_callback(
                        completed,
                        total,
                        f"Artist/title matching complete ({len(artist_title_matches)} matched)",
                    )

            # Merge all results
            all_matches = {**isrc_matches, **artist_title_matches}
            all_failures = (
                isrc_failures
                + artist_title_failures
                + no_isrc_failures
                + unprocessable_failures
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

    def _isrc_only_skip_failures(self, tracks: list[Track]) -> list[MatchFailure]:
        """Failures for the artist/title partition of an ISRC-only provider.

        Tracks with an id fail as ``NO_ISRC``; id-less ones as ``NO_METADATA``
        with ``track_id=None`` — they cannot be addressed per-track but must
        not vanish from the failure surface.
        """
        return [
            create_and_log_failure(
                track_id=t.id,
                reason=MatchFailureReason.NO_ISRC,
                service=self.service_name,
                method="isrc",
                details="Track has no ISRC and this provider matches by ISRC only",
            )
            if t.id
            else create_and_log_failure(
                track_id=None,
                reason=MatchFailureReason.NO_METADATA,
                service=self.service_name,
                method="unknown",
                details="Track has no database id and no ISRC",
            )
            for t in tracks
        ]

    async def _match_each(
        self,
        tracks: list[Track],
        method: str,
        matcher: Callable[
            [Track],
            Awaitable[tuple[RawProviderMatch | None, MatchFailure | None]],
        ],
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Run a per-track ``matcher`` over pre-partitioned tracks.

        Owns the per-track loop shell shared by subclass hooks: the
        ``track.id`` guard and the exception → ``handle_track_processing_failure``
        classification. Tracks are already validated by ``_partition_tracks``
        (the single validation point), so this does not re-check ISRC /
        artist / title.

        Args:
            tracks: Pre-partitioned, pre-validated tracks to match.
            method: Match-method label for failure records
                ("isrc" / "artist_title").
            matcher: Async per-track function returning (match, failure).

        Returns:
            Tuple of (matches dict, failures list).
        """
        matches: dict[UUID, RawProviderMatch] = {}
        failures: list[MatchFailure] = []

        for track in tracks:
            if not track.id:
                continue
            try:
                match, failure = await matcher(track)
            except Exception as e:
                failures.append(
                    handle_track_processing_failure(
                        track.id, self.service_name, method, e
                    )
                )
            else:
                if match is not None:
                    matches[track.id] = match
                if failure is not None:
                    failures.append(failure)

        return matches, failures

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
