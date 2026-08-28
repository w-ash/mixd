"""Workflow shell + partition strategies for track matching providers.

This module provides workflow orchestration for matching providers WITHOUT
business logic. All business decisions (confidence, thresholds, acceptance)
remain in the domain layer.

The split: ``BaseMatchingProvider.fetch_raw_matches_for_tracks`` is the one
workflow shell every provider shares (empty-batch guard, logging context,
failure summary, result assembly). How a provider partitions tracks and
sequences its match phases is its ``MatchStrategy``:

- ``IsrcThenArtistTitle`` — ISRC first, artist/title for the rest and for
  ISRC misses (Spotify, MusicBrainz).
- ``IsrcOnly`` — ISRC exclusively; ISRC-less tracks fail as ``NO_ISRC``
  (Apple Music, Tidal).
- ``SingleBatch`` — one batch call over the whole list, no partitioning
  (Last.fm).
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Container, Mapping
from typing import NamedTuple, Protocol
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
from src.infrastructure.connectors._shared.isrc import normalize_isrc

logger = get_logger(__name__)

type MatchOutcome = tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]
type MatchPhase = Callable[[list[Track]], Awaitable[MatchOutcome]]


def _lookup_failure_detail(
    service_label: str, code_label: str, code: str, *, batched: bool
) -> str:
    """Why a code went unanswered, naming the request that actually failed.

    A batched lookup fails every code in the request, so attributing the failure
    to the one code would misread as "this code was rejected" across a page of
    otherwise identical failures.
    """
    scope = f"the chunk holding {code_label}" if batched else code_label
    return f"{service_label} catalog lookup failed for {scope}: {code}"


def _has_isrc(track: Track) -> bool:
    """True if the track carries an ISRC."""
    return bool(track.isrc)


def _has_artist_and_title(track: Track) -> bool:
    """True if the track carries both artist and title."""
    return bool(track.artists and track.title)


def _partition_tracks(
    tracks: list[Track],
) -> tuple[list[Track], list[Track], list[Track]]:
    """Partition tracks by matching method — the single validation point.

    Tracks in the ISRC partition are guaranteed to carry an ISRC and tracks
    in the artist/title partition a valid artist/title, so phase hooks must
    not re-validate.

    Returns:
        Tuple of (isrc_tracks, artist_title_tracks, unprocessable_tracks).
    """
    isrc_tracks: list[Track] = []
    artist_title_tracks: list[Track] = []
    unprocessable_tracks: list[Track] = []

    for track in tracks:
        if _has_isrc(track):
            # ISRC takes priority
            isrc_tracks.append(track)
        elif _has_artist_and_title(track):
            # Fallback to artist/title
            artist_title_tracks.append(track)
        else:
            # Cannot process
            unprocessable_tracks.append(track)

    return isrc_tracks, artist_title_tracks, unprocessable_tracks


def _unprocessable_failures(
    tracks: list[Track], service_name: str
) -> list[MatchFailure]:
    """Failures for tracks with no usable matching metadata.

    Id-less tracks emit a failure too (track_id=None): they cannot be
    addressed per-track but must not vanish — same doctrine as the
    ISRC-only skip path.
    """
    return [
        create_and_log_failure(
            track_id=t.id,
            reason=MatchFailureReason.NO_METADATA,
            service=service_name,
            method="unknown",
            details="Track missing artist or title data"
            if t.id
            else "Track has no database id and no usable metadata",
        )
        for t in tracks
    ]


def _no_isrc_skip_failures(
    tracks: list[Track], service_name: str
) -> list[MatchFailure]:
    """Failures for the ISRC-less partition of an ISRC-only provider.

    Tracks with an id fail as ``NO_ISRC``; id-less ones as ``NO_METADATA``
    with ``track_id=None`` — they cannot be addressed per-track but must
    not vanish from the failure surface.
    """
    return [
        create_and_log_failure(
            track_id=t.id,
            reason=MatchFailureReason.NO_ISRC,
            service=service_name,
            method="isrc",
            details="Track has no ISRC and this provider matches by ISRC only",
        )
        if t.id
        else create_and_log_failure(
            track_id=None,
            reason=MatchFailureReason.NO_METADATA,
            service=service_name,
            method="unknown",
            details="Track has no database id and no ISRC",
        )
        for t in tracks
    ]


class _IsrcPhaseOutcome(NamedTuple):
    """What the shared ISRC phase decided and what it left for the tail."""

    matches: dict[UUID, RawProviderMatch]
    failures: list[MatchFailure]
    remainder: list[Track]
    completed: int


async def _run_isrc_phase(
    tracks: list[Track],
    *,
    match_by_isrc: MatchPhase,
    service_name: str,
    progress_callback: ProgressCallback | None,
    retry_misses: bool,
) -> _IsrcPhaseOutcome:
    """Partition, fail unprocessable tracks, and run the guarded ISRC phase.

    ``completed`` counts each track exactly once, when its final workflow
    outcome is decided — ``remainder`` tracks stay uncounted until the
    strategy tail decides them. With ``retry_misses``, ISRC misses that
    carry a valid artist/title join the remainder for a second-chance
    search instead of counting here.
    """
    total = len(tracks)
    isrc_tracks, rest_tracks, unprocessable_tracks = _partition_tracks(tracks)
    failures = _unprocessable_failures(unprocessable_tracks, service_name)
    completed = len(unprocessable_tracks)

    matches: dict[UUID, RawProviderMatch] = {}
    fallback_tracks: list[Track] = []
    if isrc_tracks:
        matches, isrc_failures = await match_by_isrc(isrc_tracks)
        failures = isrc_failures + failures
        if retry_misses:
            fallback_tracks = [
                t
                for t in isrc_tracks
                if t.id not in matches and _has_artist_and_title(t)
            ]
            if fallback_tracks:
                logger.info(
                    f"Falling back to artist/title for "
                    f"{len(fallback_tracks)} failed ISRC tracks"
                )
        completed += len(isrc_tracks) - len(fallback_tracks)
        if progress_callback is not None:
            await progress_callback(
                completed,
                total,
                f"ISRC matching complete ({len(matches)} matched)",
            )

    remainder = [t for t in rest_tracks if t.id not in matches] + fallback_tracks
    return _IsrcPhaseOutcome(matches, failures, remainder, completed)


class MatchStrategy(Protocol):
    """How one provider partitions tracks and sequences its match phases.

    A strategy owns partitioning, phase order, per-phase progress reports,
    and phase-failure records. The universal workflow shell
    (``BaseMatchingProvider.fetch_raw_matches_for_tracks``) stays outside.
    """

    async def run(
        self,
        tracks: list[Track],
        *,
        service_name: str,
        progress_callback: ProgressCallback | None,
    ) -> MatchOutcome:
        """Run the match phases over a non-empty track list."""
        ...


class IsrcThenArtistTitle:
    """ISRC first, then artist/title for the rest and for ISRC misses.

    ISRC misses with a valid artist/title get a second-chance search;
    tracks with neither ISRC nor artist/title fail as ``NO_METADATA``.
    """

    _match_by_isrc: MatchPhase
    _match_by_artist_title: MatchPhase

    def __init__(
        self, *, match_by_isrc: MatchPhase, match_by_artist_title: MatchPhase
    ) -> None:
        """Wire the provider's ISRC and artist/title phase hooks."""
        self._match_by_isrc = match_by_isrc
        self._match_by_artist_title = match_by_artist_title

    async def run(
        self,
        tracks: list[Track],
        *,
        service_name: str,
        progress_callback: ProgressCallback | None,
    ) -> MatchOutcome:
        """Run the ISRC phase, then artist/title over the undecided remainder."""
        phase = await _run_isrc_phase(
            tracks,
            match_by_isrc=self._match_by_isrc,
            service_name=service_name,
            progress_callback=progress_callback,
            retry_misses=True,
        )

        artist_title_matches: dict[UUID, RawProviderMatch] = {}
        artist_title_failures: list[MatchFailure] = []
        if phase.remainder:
            (
                artist_title_matches,
                artist_title_failures,
            ) = await self._match_by_artist_title(phase.remainder)
            if progress_callback is not None:
                await progress_callback(
                    phase.completed + len(phase.remainder),
                    len(tracks),
                    f"Artist/title matching complete ({len(artist_title_matches)} matched)",
                )

        return (
            {**phase.matches, **artist_title_matches},
            phase.failures + artist_title_failures,
        )


class IsrcOnly:
    """ISRC exclusively — no artist/title phase exists at all.

    Conservative providers (Apple Music, Tidal) use this: ISRC-less tracks
    fail as ``NO_ISRC`` instead of entering a name search, and ISRC misses
    keep the failure the ISRC hook already reported — no second-chance
    search, so no double-counted failures.
    """

    _match_by_isrc: MatchPhase

    def __init__(self, *, match_by_isrc: MatchPhase) -> None:
        """Wire the provider's ISRC phase hook."""
        self._match_by_isrc = match_by_isrc

    async def run(
        self,
        tracks: list[Track],
        *,
        service_name: str,
        progress_callback: ProgressCallback | None,
    ) -> MatchOutcome:
        """Run the ISRC phase; fail the ISRC-less remainder without a search."""
        phase = await _run_isrc_phase(
            tracks,
            match_by_isrc=self._match_by_isrc,
            service_name=service_name,
            progress_callback=progress_callback,
            retry_misses=False,
        )

        no_isrc_failures = _no_isrc_skip_failures(phase.remainder, service_name)
        if progress_callback is not None and phase.remainder:
            await progress_callback(
                phase.completed + len(phase.remainder),
                len(tracks),
                f"Skipped {len(phase.remainder)} tracks without "
                "ISRC (ISRC-only provider)",
            )

        return phase.matches, phase.failures + no_isrc_failures


class SingleBatch:
    """One batch call over the whole track list — no partitioning.

    A batch API answers every track at once (Last.fm), so there are no
    phases: the hook runs once, a hook exception fails every id-bearing
    track, and one progress report closes the workflow.
    """

    _match_batch: MatchPhase
    _label: str

    def __init__(self, *, match_batch: MatchPhase, label: str) -> None:
        """Wire the provider's batch hook and its progress display label."""
        self._match_batch = match_batch
        self._label = label

    async def run(
        self,
        tracks: list[Track],
        *,
        service_name: str,
        progress_callback: ProgressCallback | None,
    ) -> MatchOutcome:
        """Run the one batch phase; a hook exception fails the whole batch."""
        matches: dict[UUID, RawProviderMatch] = {}
        failures: list[MatchFailure] = []
        try:
            matches, failures = await self._match_batch(tracks)
        except Exception as e:
            # Batch API failed - all tracks failed
            failures = [
                handle_track_processing_failure(
                    track.id, service_name, "batch_lookup", e
                )
                for track in tracks
                if track.id
            ]

        if progress_callback is not None:
            await progress_callback(
                len(tracks),
                len(tracks),
                f"{self._label} batch matching complete ({len(matches)} matched)",
            )

        return matches, failures


class BaseMatchingProvider(ABC):
    """Shared workflow shell for matching providers — TECHNICAL concerns only.

    This class contains workflow orchestration and technical utilities.
    NO business logic (confidence, thresholds, acceptance decisions).

    Architecture compliance:
    - Infrastructure layer: Data extraction and API communication
    - Domain layer: Business logic (confidence, thresholds, evaluation)
    - Application layer: Orchestration of infrastructure → domain flow

    Subclasses must implement:
    - service_name: Service identifier property
    - _match_strategy(): the partition/phase strategy wired to the
      provider's own phase hook methods
    """

    @property
    @abstractmethod
    def service_name(self) -> str:
        """Service identifier for logging and failure tracking."""
        ...

    @abstractmethod
    def _match_strategy(self) -> MatchStrategy:
        """The partition/phase strategy for this provider's workflow."""
        ...

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Track],
        progress_callback: ProgressCallback | None = None,
        **additional_options: object,
    ) -> ProviderMatchResult:
        """Run the matching workflow shell around the provider's strategy.

        The shell owns what every provider does identically: the empty-batch
        guard, the logging context, the failure summary, and result assembly.
        Partitioning and phase sequencing belong to ``_match_strategy()``.

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

        with logging_context(
            operation=f"match_{self.service_name}", track_count=len(tracks)
        ):
            matches, failures = await self._match_strategy().run(
                tracks,
                service_name=self.service_name,
                progress_callback=progress_callback,
            )

            final_result = ProviderMatchResult(matches=matches, failures=failures)

            # Log summary
            log_failure_summary(
                self.service_name, len(final_result.matches), len(final_result.failures)
            )
            logger.info(
                f"Found {len(final_result.matches)} matches from {len(tracks)} tracks"
            )

            return final_result

    def _normalized_isrc_by_track(self, tracks: list[Track]) -> dict[UUID, str]:
        """Map track id -> normalized ISRC for a pre-partitioned ISRC batch.

        Skips id-less tracks and codes that normalize to nothing.
        """
        isrc_by_track: dict[UUID, str] = {}
        for track in tracks:
            if not track.id:
                continue
            normalized = normalize_isrc(track.isrc or "")
            if normalized:
                isrc_by_track[track.id] = normalized
        return isrc_by_track

    def _correlate_by_code[CandidateT](
        self,
        tracks: list[Track],
        *,
        code_of: Mapping[UUID, str],
        candidates_by_code: Mapping[str, CandidateT],
        failed_codes: Container[str],
        make_match: Callable[[Track, CandidateT], RawProviderMatch],
        service_label: str,
        method: str,
        code_label: str,
        batched: bool = False,
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Correlate a batched code lookup back onto its tracks.

        The batch counterpart to ``_match_each``: the caller does one lookup per
        distinct code, and this decides per track whether the code went
        unanswered (``API_ERROR``, the code is in ``failed_codes``) or was
        answered with nothing (``NO_RESULTS``). Keeping that distinction here is
        what stops each connector re-deriving the subtle half.

        Args:
            tracks: Pre-partitioned, pre-validated tracks.
            code_of: track id -> the code looked up for it.
            candidates_by_code: code -> whatever the lookup produced. A code
                absent here, or mapping to a falsy value, counts as no result.
            failed_codes: codes whose lookup raised.
            make_match: builds the raw match from a track and its candidate.
            service_label: display name used in failure details.
            method: match-method label stamped on every failure ("isrc").
            code_label: what the code is called in failure details ("ISRC").
            batched: the caller looks codes up in batches, so one failed request
                fails every code it carried — the message says so rather than
                reading as a per-code miss.
        """
        matches: dict[UUID, RawProviderMatch] = {}
        failures: list[MatchFailure] = []
        for track in tracks:
            if not track.id:
                continue
            code = code_of.get(track.id, "")
            candidate = candidates_by_code.get(code)
            if not candidate:
                failed = code in failed_codes
                failures.append(
                    create_and_log_failure(
                        track_id=track.id,
                        reason=(
                            MatchFailureReason.API_ERROR
                            if failed
                            else MatchFailureReason.NO_RESULTS
                        ),
                        service=self.service_name,
                        method=method,
                        details=(
                            _lookup_failure_detail(
                                service_label, code_label, code, batched=batched
                            )
                            if failed
                            else f"No {service_label} results for {code_label}: {code}"
                        ),
                    )
                )
                continue
            matches[track.id] = make_match(track, candidate)
        return matches, failures

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
        classification. Tracks are already validated by the strategy partition
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
