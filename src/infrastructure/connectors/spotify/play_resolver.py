"""Spotify-specific connector play resolver with rich metadata preservation.

Handles Spotify's comprehensive metadata including behavioral data, technical metadata,
and sophisticated duration-based filtering.
"""

from collections.abc import Callable
from statistics import median
from typing import Final
from uuid import UUID

from attrs import evolve

from src.config import get_logger, settings
from src.domain.entities import ConnectorTrackPlay, Track, TrackPlay
from src.domain.entities.shared import JsonValue
from src.domain.matching.play_projection import (
    build_play_context,
    spotify_id_from_uri,
)
from src.domain.repositories.play import PlayResolutionOutcome, ResolutionMetrics
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify.inward_resolver import (
    FallbackHint,
    SpotifyInwardResolver,
)

logger = get_logger(__name__)


# The export's ``reason_end`` for a play that reached the end of the track,
# as opposed to being skipped, cut off, or ended by the app closing. Only
# these plays say anything about how long the track runs.
TRACKDONE: Final[str] = "trackdone"


def _ran_to_completion(connector_play: ConnectorTrackPlay) -> bool:
    """Did this play reach the end of the track, per the export's own verdict?"""
    return connector_play.service_metadata.get("reason_end") == TRACKDONE


def _median_ms(completed_play_ms: list[int] | None) -> int | None:
    """The batch's estimate of a track's length, or None if it observed none.

    Median rather than max or mean: ``ms_played`` is time spent in the player,
    not distance through the track, so a listener who seeks backwards inflates
    a single play (the import's convergence findings record a >6h outlier) and
    one who seeks forwards deflates it, both while still ending ``trackdone``.
    The median ignores either outlier as long as it is the minority, and a
    single completed play is trivially its own median.
    """
    if not completed_play_ms:
        return None
    return round(median(completed_play_ms))


def _is_incognito(connector_play: ConnectorTrackPlay) -> bool:
    """Was this play made in a private session, per the export's own flag?"""
    return bool(connector_play.service_metadata.get("incognito_mode", False))


def should_include_spotify_play(
    ms_played: int,
    track_duration_ms: int | None,
    track_name: str | None = None,
    artist_name: str | None = None,
) -> bool:
    """Apply Spotify-specific play filtering based on duration.

    Spotify duration filtering rules:
    - Rule 1: All plays >= 4 minutes are always included
    - Rule 2: For plays < 4 minutes, use 50% threshold for tracks < 8 minutes
    """
    # Get configuration with type-safe defaults
    threshold_ms = settings.import_settings.play_threshold_ms
    threshold_percentage = settings.import_settings.play_threshold_percentage

    # Rule 1: All plays >= 4 minutes are always included
    if ms_played >= threshold_ms:
        return True

    # Rule 2: For plays < 4 minutes, use 50% threshold for tracks < 8 minutes
    if track_duration_ms is None:
        track_info = (
            f"{artist_name} - {track_name}"
            if artist_name and track_name
            else "unknown track"
        )
        logger.warning(f"Missing duration for filtering: {track_info}")
        return False  # < 4 minutes and no duration info = exclude

    # For tracks >= 8 minutes, 4-minute threshold already failed above, so exclude
    if track_duration_ms >= threshold_ms * 2:  # 8 minutes
        return False

    # For tracks < 8 minutes, use 50% threshold
    percentage_threshold = int(track_duration_ms * threshold_percentage)
    return ms_played >= percentage_threshold


class SpotifyConnectorPlayResolver:
    """Spotify-specific connector play resolver with rich metadata preservation.

    Preserves Spotify's comprehensive metadata:
    - Track duration for sophisticated filtering
    - Platform, country, reason_start/end behavioral data
    - Shuffle, skip, offline status
    - Full Spotify URI preservation
    - ISRC and detailed album information
    """

    spotify_connector: SpotifyConnector
    _inward_resolver: SpotifyInwardResolver
    _completed_play_ms_by_id: dict[str, list[int]]

    def __init__(self, spotify_connector: SpotifyConnector | None = None):
        """Initialize with Spotify connector for track resolution."""
        self.spotify_connector = spotify_connector or SpotifyConnector()
        self._inward_resolver = SpotifyInwardResolver(
            spotify_connector=self.spotify_connector
        )
        # Run-scoped, not call-scoped: the orchestrator builds one resolver per
        # service per import and then calls it once per 50-play chunk, so this
        # accumulates across the chunks of a single import and is discarded
        # with the resolver when that import ends. See ``_accumulate_completed_ms``.
        self._completed_play_ms_by_id = {}

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> PlayResolutionOutcome:
        """Resolve Spotify connector plays with full metadata preservation.

        Incognito plays are partitioned out BEFORE resolution: an excluded
        play must not cost an API fetch or create a canonical track, and an
        all-incognito chunk never reaches the inward resolver at all. One
        semantic consequence, deliberate: a play that is both incognito and
        short counts as ``incognito_excluded`` (pre-v0.10.2.9 it counted as
        ``duration_excluded``) — the duration rule needs the canonical
        ``duration_ms`` an excluded play no longer resolves.
        """
        _ = progress_callback  # Keep for future progress tracking integration
        if not connector_plays:
            return self._empty_outcome()

        # Step 1: Partition out incognito plays, then extract unique Spotify
        # track IDs + fallback hints from the eligible ones. Completed-play
        # evidence still accumulates over the WHOLE chunk: a trackdone
        # duration from an incognito play is evidence about the track's
        # length, not about the play's eligibility.
        eligible = [cp for cp in connector_plays if not _is_incognito(cp)]
        filtering_stats: ResolutionMetrics = {
            "raw_plays": len(connector_plays),
            "accepted_plays": 0,
            "duration_excluded": 0,
            "incognito_excluded": len(connector_plays) - len(eligible),
            "error_count": 0,
            "resolution_failures": [],
        }

        unique_spotify_ids, fallback_hints = self._extract_ids_and_hints(
            eligible, evidence_plays=connector_plays
        )
        if not unique_spotify_ids:
            if eligible:
                logger.warning("No valid Spotify track IDs found in connector plays")
            # No inward-resolver call, but the chunk's counts are real —
            # ``_empty_outcome()`` would zero raw_plays/incognito_excluded
            # and under-report an excluded-only chunk. Eligible plays that
            # reached here carry no extractable id (malformed URIs): they are
            # errors, exactly as the main loop records them — without this
            # the sums stop reconciling (raw = accepted + excluded + errors).
            for connector_play in eligible:
                self._record_resolution_failure(filtering_stats, connector_play, None)
            return PlayResolutionOutcome(
                track_plays=[],
                metrics={**self._create_empty_metrics(), **filtering_stats},
                resolutions=(),
            )

        # Step 2: Resolve Spotify track IDs to canonical tracks
        (
            canonical_tracks_map,
            canonical_track_metrics,
        ) = await self._resolve_spotify_ids_to_canonical_tracks(
            unique_spotify_ids, uow, user_id=user_id, fallback_hints=fallback_hints
        )

        # Step 3: Create TrackPlay objects with Spotify's rich metadata
        track_plays: list[TrackPlay] = []
        resolutions: list[tuple[ConnectorTrackPlay, UUID]] = []

        for connector_play in eligible:
            spotify_id = self._extract_spotify_id_from_connector_play(connector_play)
            canonical_track = (
                canonical_tracks_map.get(spotify_id) if spotify_id else None
            )

            if not canonical_track or not canonical_track.id:
                self._record_resolution_failure(
                    filtering_stats, connector_play, spotify_id
                )
                continue

            if self._duration_excluded(connector_play, canonical_track):
                filtering_stats["duration_excluded"] += 1
                duration_info = (
                    f"{canonical_track.duration_ms / 60000:.2f}"
                    if canonical_track.duration_ms
                    else "?"
                )
                # A duration skip implies ms_played is not None (the guard
                # lives in _duration_excluded); `or 0` only satisfies the
                # type checker.
                ms_played = connector_play.ms_played or 0
                logger.debug(
                    f"Skipped (duration): {connector_play.track_name} - "
                    f"{ms_played / 60000:.2f}/{duration_info}min"
                )
                continue

            filtering_stats["accepted_plays"] += 1
            resolutions.append((connector_play, canonical_track.id))
            track_plays.append(
                TrackPlay(
                    track_id=canonical_track.id,
                    service="spotify",
                    played_at=connector_play.played_at,
                    user_id=user_id,
                    ms_played=connector_play.ms_played,
                    context=self._build_context(connector_play, spotify_id),
                    import_timestamp=connector_play.import_timestamp,
                    import_source=connector_play.import_source or "spotify_export",
                    import_batch_id=connector_play.import_batch_id,
                )
            )

        spotify_metrics = self._assemble_metrics(
            filtering_stats,
            canonical_track_metrics,
            unique_ids_count=len(unique_spotify_ids),
            tracks_resolved=len(canonical_tracks_map),
        )

        logger.info(
            "Processed Spotify connector plays with rich metadata preservation",
            total_plays=len(connector_plays),
            unique_tracks=len(unique_spotify_ids),
            resolved_tracks=len(canonical_tracks_map),
            accepted_plays=filtering_stats["accepted_plays"],
            duration_excluded=filtering_stats["duration_excluded"],
            incognito_excluded=filtering_stats["incognito_excluded"],
            error_count=filtering_stats["error_count"],
            new_tracks=canonical_track_metrics["new_tracks_count"],
            updated_tracks=canonical_track_metrics["updated_tracks_count"],
        )

        return PlayResolutionOutcome(
            track_plays=track_plays,
            metrics=spotify_metrics,
            resolutions=tuple(resolutions),
        )

    @staticmethod
    def _record_resolution_failure(
        filtering_stats: ResolutionMetrics,
        connector_play: ConnectorTrackPlay,
        spotify_id: str | None,
    ) -> None:
        """Count one eligible play that produced no canonical track.

        The single failure-record shape for both paths that drop an eligible
        play: an id that resolved to nothing in the main loop, and an id-less
        play (malformed URI) — including the all-id-less chunk's early
        return, which previously vanished such plays from the metrics
        entirely. The orchestrator caps the accumulated list downstream
        (``_MAX_RECORDED_RESOLUTION_FAILURES``); per-chunk lists stay whole.
        """
        # .get/.setdefault rather than [] — the TypedDict's keys are
        # not-required, and this helper sees the dict without the literal
        # construction the call sites narrow from.
        filtering_stats["error_count"] = filtering_stats.get("error_count", 0) + 1
        filtering_stats.setdefault("resolution_failures", []).append({
            "track": f"{connector_play.artist_name} - {connector_play.track_name}",
            "spotify_id": spotify_id or "",
            "reason": "track_resolution_failed",
        })
        logger.warning(
            f"Track not resolved: {connector_play.artist_name} - {connector_play.track_name}"
        )

    def _extract_ids_and_hints(
        self,
        connector_plays: list[ConnectorTrackPlay],
        *,
        evidence_plays: list[ConnectorTrackPlay],
    ) -> tuple[list[str], dict[str, FallbackHint]]:
        """Extract unique Spotify track IDs + fallback hints.

        Ids and hint names come only from ``connector_plays`` — the eligible
        plays that may cost a resolution. ``evidence_plays`` is the whole
        chunk, incognito included, and feeds only the completed-play
        accumulator: an excluded play must not trigger a fetch, but its
        trackdone duration is still evidence of the track's length. Names
        come from the first eligible play in this chunk carrying the id; the
        length estimate is derived from the run's accumulator rather than
        from this chunk alone (see ``_accumulate_completed_ms``).
        """
        for cp in evidence_plays:
            sid = self._extract_spotify_id_from_connector_play(cp)
            if sid:
                self._accumulate_completed_ms(sid, cp)

        unique_ids_set: set[str] = set()
        fallback_hints: dict[str, FallbackHint] = {}
        for cp in connector_plays:
            sid = self._extract_spotify_id_from_connector_play(cp)
            if not sid:
                continue
            unique_ids_set.add(sid)
            if sid not in fallback_hints and cp.artist_name and cp.track_name:
                fallback_hints[sid] = FallbackHint(
                    artist_name=cp.artist_name, track_name=cp.track_name
                )

        return list(unique_ids_set), {
            sid: evolve(
                hint,
                completed_play_ms_estimate=_median_ms(
                    self._completed_play_ms_by_id.get(sid)
                ),
            )
            for sid, hint in fallback_hints.items()
        }

    def _accumulate_completed_ms(self, sid: str, cp: ConnectorTrackPlay) -> None:
        """Add a completed play's duration to the run's evidence for ``sid``.

        The estimate stands in for a track-length field the GDPR export does
        not have, and it is the only thing that can veto a search fallback for
        picking a radio edit over the album cut. Derived from one 50-play chunk
        it was routinely absent or wrong: a track played to completion five
        times across a decade of history has its plays scattered over many
        chunks, so the chunk holding its *dead* id often held none of them and
        asserted no length at all.

        Accumulating per run fixes the common case without pretending to fix
        all of it — plays that arrive in a *later* chunk than the one that
        resolves the id still cannot contribute, because the hint has to be
        built before the resolution it feeds. Only prior and current chunks
        count, which for a chronologically ordered export is most of them.
        """
        if _ran_to_completion(cp) and cp.ms_played:
            self._completed_play_ms_by_id.setdefault(sid, []).append(cp.ms_played)

    def _duration_excluded(
        self, connector_play: ConnectorTrackPlay, canonical_track: Track
    ) -> bool:
        """True when the resolved play is too short to count as listened.

        Runs post-resolution deliberately — unlike the incognito partition —
        because the 50% rule needs the canonical ``duration_ms``, which only
        a resolved track carries.
        """
        return connector_play.ms_played is not None and not (
            should_include_spotify_play(
                connector_play.ms_played,
                canonical_track.duration_ms,
                connector_play.track_name,
                connector_play.artist_name,
            )
        )

    def _build_context(
        self, connector_play: ConnectorTrackPlay, spotify_id: str | None
    ) -> dict[str, JsonValue]:
        """Build the persisted play context via the domain builder.

        The domain builder is the single implementation the projection also
        uses; the only run-scoped addition here is the per-run resolution
        method (redirect/fallback), which the ledger cannot reconstruct —
        the domain builder records the stable marker for those instead.
        """
        context = build_play_context(connector_play)
        if spotify_id:
            context["resolution_method"] = self._inward_resolver.get_resolution_method(
                spotify_id
            )
        return context

    def _assemble_metrics(
        self,
        filtering_stats: ResolutionMetrics,
        canonical_track_metrics: dict[str, int],
        *,
        unique_ids_count: int,
        tracks_resolved: int,
    ) -> ResolutionMetrics:
        """Combine per-play filtering stats with canonical-resolution counts."""
        return {
            **filtering_stats,
            "new_tracks_count": canonical_track_metrics["new_tracks_count"],
            "updated_tracks_count": canonical_track_metrics["updated_tracks_count"],
            "unique_tracks_processed": unique_ids_count,
            "tracks_resolved": tracks_resolved,
            "fallback_resolved": canonical_track_metrics["fallback_resolved"],
            "redirect_resolved": canonical_track_metrics["redirect_resolved"],
            "dead_ids_unresolved": canonical_track_metrics["dead_ids_unresolved"],
            "isrc_suspect_deferred": len(
                self._inward_resolver.isrc_suspect_deferred_ids
            ),
        }

    def _extract_spotify_id_from_connector_play(
        self, connector_play: ConnectorTrackPlay
    ) -> str | None:
        """Extract Spotify track ID from ConnectorTrackPlay metadata."""
        # Try service metadata first
        track_uri = connector_play.service_metadata.get("track_uri")
        if isinstance(track_uri, str):
            return self._extract_spotify_id_from_uri(track_uri)

        # Fallback to connector_track_identifier
        if connector_play.connector_track_identifier.startswith("spotify:track:"):
            return self._extract_spotify_id_from_uri(
                connector_play.connector_track_identifier
            )

        return None

    def _extract_spotify_id_from_uri(self, spotify_uri: str) -> str | None:
        """Extract Spotify track ID from a Spotify URI (domain single source)."""
        return spotify_id_from_uri(spotify_uri) if spotify_uri else None

    async def _resolve_spotify_ids_to_canonical_tracks(
        self,
        spotify_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        fallback_hints: dict[str, FallbackHint] | None = None,
    ) -> tuple[dict[str, Track], dict[str, int]]:
        """Resolve Spotify track IDs to canonical tracks.

        Delegates to SpotifyInwardResolver which handles:
        - Bulk lookup of existing connector mappings
        - Batch Spotify API fetch for missing tracks
        - Fallback search for dead IDs using artist+title hints
        """
        tracks_map, metrics = await self._inward_resolver.resolve_to_canonical_tracks(
            spotify_ids, uow, user_id=user_id, fallback_hints=fallback_hints
        )
        return tracks_map, {
            "new_tracks_count": metrics.created,
            "updated_tracks_count": metrics.existing,
            "dead_ids_unresolved": metrics.failed,
            "redirect_resolved": metrics.redirects,
            "fallback_resolved": metrics.fallbacks,
        }

    def _create_empty_metrics(self) -> ResolutionMetrics:
        """Create empty metrics dictionary."""
        return {
            "raw_plays": 0,
            "accepted_plays": 0,
            "duration_excluded": 0,
            "incognito_excluded": 0,
            "error_count": 0,
            "resolution_failures": [],
            "new_tracks_count": 0,
            "updated_tracks_count": 0,
            "unique_tracks_processed": 0,
            "tracks_resolved": 0,
            "fallback_resolved": 0,
            "redirect_resolved": 0,
            "dead_ids_unresolved": 0,
            "isrc_suspect_deferred": 0,
        }

    def _empty_outcome(self) -> PlayResolutionOutcome:
        return PlayResolutionOutcome(
            track_plays=[],
            metrics=self._create_empty_metrics(),
            resolutions=(),
        )
