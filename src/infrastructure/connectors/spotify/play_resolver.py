"""Spotify-specific connector play resolver with rich metadata preservation.

Handles Spotify's comprehensive metadata including behavioral data, technical metadata,
and sophisticated duration-based filtering.
"""

from statistics import median
from typing import Final
from uuid import UUID

from attrs import evolve

from src.config import get_logger, settings
from src.domain.entities import (
    ConnectorTrackPlay,
    PlayExclusionReason,
    Track,
)
from src.domain.entities.shared import JsonValue
from src.domain.matching.play_projection import (
    build_play_context,
    group_into_islands,
    spotify_id_from_uri,
)
from src.domain.repositories.play import PlayResolutionOutcome, ResolutionMetrics
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.connector_play_resolver import (
    build_play_outcome,
    empty_play_metrics,
)
from src.infrastructure.connectors._shared.inward_track_resolver import (
    TrackResolutionMetrics,
)
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

    That is Last.fm's own scrobble rule, and the parity is load-bearing rather
    than coincidental: it is very likely why the two channels converge on the
    same listens at all. Loosening it here would count plays Last.fm never
    scrobbles, manufacturing single-channel plays that read as merge failures
    in every future audit — so treat the two numbers as one shared definition,
    not as a Spotify setting that happens to match.

    One divergence, deliberate: Last.fm refuses to scrobble any track under 30
    seconds and this rule has no such floor, so a short interlude played start
    to finish counts (120 plays across 71 tracks in the production corpus).
    A fully-played track is a listen whatever its length, and erring generous
    is right for a product about owning your own listening data. Those plays
    can never have a Last.fm twin, so the audit reports them as a structural
    blind spot rather than as pairing misses — ``audit_play_integrity.py
    --check short_track_blind_spot``.
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
        if not connector_plays:
            return self._empty_outcome()

        # Each play's Spotify id, parsed exactly once for the whole pass —
        # the evidence pass, the hint pass, the island check, the accept
        # loop, and the failure records all read this map.
        ids_by_play: dict[UUID, str | None] = {
            cp.id: self._extract_spotify_id_from_connector_play(cp)
            for cp in connector_plays
        }

        def _spotify_id_detail(connector_play: ConnectorTrackPlay) -> dict[str, str]:
            return {"spotify_id": ids_by_play[connector_play.id] or ""}

        # Step 1: Partition out incognito plays, then extract unique Spotify
        # track IDs + fallback hints from the eligible ones. Completed-play
        # evidence still accumulates over the WHOLE chunk: a trackdone
        # duration from an incognito play is evidence about the track's
        # length, not about the play's eligibility.
        eligible: list[ConnectorTrackPlay] = []
        # Every play excluded before handoff to the shared builder records
        # why, so the ledger can distinguish a deliberate skip from a failure
        # to identify the track.
        pre_exclusions: list[tuple[ConnectorTrackPlay, PlayExclusionReason]] = []
        for connector_play in connector_plays:
            if _is_incognito(connector_play):
                pre_exclusions.append((connector_play, "incognito"))
            else:
                eligible.append(connector_play)
        incognito_excluded = len(pre_exclusions)

        unique_spotify_ids, fallback_hints = self._extract_ids_and_hints(
            eligible, evidence_plays=connector_plays, ids_by_play=ids_by_play
        )
        if not unique_spotify_ids:
            if eligible:
                logger.warning("No valid Spotify track IDs found in connector plays")
            # No inward-resolver call, but the chunk's counts are real —
            # ``_empty_outcome()`` would zero raw_plays/incognito_excluded
            # and under-report an excluded-only chunk. Eligible plays that
            # reached here carry no extractable id (malformed URIs): handed
            # to the shared builder as unresolved pairs, they are errors,
            # exactly as the main path records them — without this the sums
            # stop reconciling (raw = accepted + excluded + errors).
            return build_play_outcome(
                [(connector_play, None) for connector_play in eligible],
                service="spotify",
                user_id=user_id,
                default_import_source="spotify_export",
                resolution_metrics=TrackResolutionMetrics(),
                failure_detail=_spotify_id_detail,
                extra_exclusions=pre_exclusions,
                extra_metrics=self._assemble_metrics(
                    TrackResolutionMetrics(),
                    unique_ids_count=0,
                    tracks_resolved=0,
                    duration_excluded=0,
                    incognito_excluded=incognito_excluded,
                    isrc_suspect_deferred=0,
                ),
            )

        # Step 2: Resolve Spotify track IDs to canonical tracks. The inward
        # resolver handles bulk mapping lookup, batch API fetch for missing
        # tracks, and fallback search for dead IDs using artist+title hints.
        (
            canonical_tracks_map,
            canonical_track_metrics,
        ) = await self._inward_resolver.resolve_to_canonical_tracks(
            unique_spotify_ids, uow, user_id=user_id, hints=fallback_hints
        )

        # Step 3: Decide which listens clear the duration threshold. Plays
        # that fall short are excluded here, before handoff — the shared
        # builder then records unresolved plays and builds the TrackPlay
        # rows for the rest.
        listened_enough = self._listened_enough(
            eligible, canonical_tracks_map, ids_by_play
        )
        resolved: list[tuple[ConnectorTrackPlay, Track | None]] = []
        duration_excluded = 0

        for connector_play in eligible:
            spotify_id = ids_by_play[connector_play.id]
            canonical_track = (
                canonical_tracks_map.get(spotify_id) if spotify_id else None
            )

            if not canonical_track or not canonical_track.id:
                resolved.append((connector_play, None))
                continue

            if connector_play.id not in listened_enough:
                duration_excluded += 1
                pre_exclusions.append((connector_play, "too_short"))
                duration_info = (
                    f"{canonical_track.duration_ms / 60000:.2f}"
                    if canonical_track.duration_ms
                    else "?"
                )
                # A duration skip implies ms_played is not None (an island
                # holding an unmeasured play is admitted whole in
                # ``_listened_enough``); `or 0` only satisfies the type checker.
                ms_played = connector_play.ms_played or 0
                logger.debug(
                    f"Skipped (duration): {connector_play.track_name} - "
                    f"{ms_played / 60000:.2f}/{duration_info}min"
                )
                continue

            resolved.append((connector_play, canonical_track))

        outcome = build_play_outcome(
            resolved,
            service="spotify",
            user_id=user_id,
            default_import_source="spotify_export",
            resolution_metrics=canonical_track_metrics,
            failure_detail=_spotify_id_detail,
            build_context=lambda connector_play: self._build_context(
                connector_play, ids_by_play[connector_play.id]
            ),
            extra_exclusions=pre_exclusions,
            extra_metrics=self._assemble_metrics(
                canonical_track_metrics,
                unique_ids_count=len(unique_spotify_ids),
                tracks_resolved=len(canonical_tracks_map),
                duration_excluded=duration_excluded,
                incognito_excluded=incognito_excluded,
                isrc_suspect_deferred=len(
                    self._inward_resolver.isrc_suspect_deferred_ids
                ),
            ),
        )

        logger.info(
            "Processed Spotify connector plays with rich metadata preservation",
            total_plays=len(connector_plays),
            unique_tracks=len(unique_spotify_ids),
            resolved_tracks=len(canonical_tracks_map),
            accepted_plays=outcome.metrics.get("accepted_plays", 0),
            duration_excluded=duration_excluded,
            incognito_excluded=incognito_excluded,
            error_count=outcome.metrics.get("error_count", 0),
            new_tracks=canonical_track_metrics.created,
            updated_tracks=canonical_track_metrics.existing,
        )

        return outcome

    def _extract_ids_and_hints(
        self,
        connector_plays: list[ConnectorTrackPlay],
        *,
        evidence_plays: list[ConnectorTrackPlay],
        ids_by_play: dict[UUID, str | None],
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
        ``ids_by_play`` is the chunk's once-parsed id map and must cover
        both play lists.
        """
        for cp in evidence_plays:
            sid = ids_by_play[cp.id]
            if sid:
                self._accumulate_completed_ms(sid, cp)

        unique_ids_set: set[str] = set()
        fallback_hints: dict[str, FallbackHint] = {}
        for cp in connector_plays:
            sid = ids_by_play[cp.id]
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

    def _listened_enough(
        self,
        eligible: list[ConnectorTrackPlay],
        canonical_tracks_map: dict[str, Track],
        ids_by_play: dict[UUID, str | None],
    ) -> set[UUID]:
        """Ids of the plays whose *listen* clears the duration threshold.

        The unit judged is the listening island, not the record (v0.10.3 C9).
        A track interrupted into three fragments is one listen the listener
        heard most of; asking the 50% question of each fragment separately
        fails it three times and drops the listen from their history
        altogether. The island partition comes from the domain
        (:func:`group_into_islands`) precisely so the answer here matches the
        one the projection will reach when it consolidates the same segments
        into one canonical play — including its duplicate pass, so a listen
        written down twice is weighed once and admitted (or dropped) whole.

        Runs post-resolution deliberately — unlike the incognito partition —
        because the 50% rule needs the canonical ``duration_ms``, which only a
        resolved track carries. An island holding a play with no ``ms_played``
        at all is admitted whole: the rule has nothing to weigh, which is the
        same verdict the per-play check reached.

        One boundary effect, accepted: the resolver sees the import's plays a
        chunk at a time, so an island straddling a chunk edge is judged in
        halves. That can leave a rescued listen short, never doubled — the
        projection islands whatever rows carry a resolution, so both halves
        land in one canonical play regardless of how they were admitted.
        """
        admitted: set[UUID] = set()
        for island in group_into_islands(eligible):
            listen = island.representative
            spotify_id = ids_by_play[listen.id]
            canonical_track = (
                canonical_tracks_map.get(spotify_id) if spotify_id else None
            )
            if canonical_track is None:
                # Never identified, so it is a resolution failure rather than a
                # short listen; the shared builder records it as one.
                continue
            # The representative carries the island's summed listened time, or
            # None when its channel reported none — nothing to weigh, which is
            # the same verdict the per-play check reached.
            if listen.ms_played is None or should_include_spotify_play(
                listen.ms_played,
                canonical_track.duration_ms,
                listen.track_name,
                listen.artist_name,
            ):
                admitted.update(island.member_ids)
        return admitted

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

    @staticmethod
    def _assemble_metrics(
        resolution_metrics: TrackResolutionMetrics,
        *,
        unique_ids_count: int,
        tracks_resolved: int,
        duration_excluded: int,
        incognito_excluded: int,
        isrc_suspect_deferred: int,
    ) -> ResolutionMetrics:
        """Spotify-specific tallies layered over the shared base metrics.

        The one place the Spotify metric key list exists — the main path,
        the excluded-only early return, and the empty outcome all pass
        through it as ``extra_metrics``. Reuse/suppression/persist counts
        are carried so a chunk's timings can be read against its shape: a
        creation-heavy chunk and a reuse-heavy one cost very differently.
        ``isrc_suspect_deferred`` is a parameter rather than a live read
        because the inward resolver's set describes its most recent call —
        a chunk that never called it reports 0.
        """
        return {
            "duration_excluded": duration_excluded,
            "incognito_excluded": incognito_excluded,
            "unique_tracks_processed": unique_ids_count,
            "tracks_resolved": tracks_resolved,
            "fallback_resolved": resolution_metrics.fallbacks,
            "redirect_resolved": resolution_metrics.redirects,
            "dead_ids_unresolved": resolution_metrics.failed,
            "reused_tracks": resolution_metrics.reused,
            "suppressed": resolution_metrics.suppressed,
            "degraded_persists": resolution_metrics.degraded_persists,
            "write_failed": resolution_metrics.write_failed,
            "isrc_suspect_deferred": isrc_suspect_deferred,
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

    def _empty_outcome(self) -> PlayResolutionOutcome:
        """Outcome for a chunk that held nothing at all."""
        return PlayResolutionOutcome(
            track_plays=[],
            metrics=empty_play_metrics(
                self._assemble_metrics(
                    TrackResolutionMetrics(),
                    unique_ids_count=0,
                    tracks_resolved=0,
                    duration_excluded=0,
                    incognito_excluded=0,
                    isrc_suspect_deferred=0,
                )
            ),
            resolutions=(),
        )
