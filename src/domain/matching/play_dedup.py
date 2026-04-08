"""Pure cross-source play history deduplication.

Identifies duplicate listening events across music services (e.g., a Spotify play
that was also scrobbled to Last.fm) and merges them into a single canonical record.

Key insight: services record timestamps differently:
- Spotify ``ts`` = END time (when playback stopped)
- Last.fm ``date.uts`` = START time (when track began playing)

Before comparing, Spotify plays are normalized to start time via
``played_at - ms_played``. After normalization, a tight tolerance window
(default 30s) identifies cross-source duplicates.
"""

from collections import defaultdict
from collections.abc import Mapping
from datetime import timedelta
from typing import Final
from uuid import UUID

from attrs import define, field

from src.domain.entities import TrackPlay
from src.domain.entities.shared import JsonValue

# Cross-service deduplication parameters (domain business rules).
# Timestamp semantics differ by service:
#   Spotify ts = END time (when playback stopped)
#   Last.fm date.uts = START time (when track began playing)
# Spotify plays are normalized to start time via played_at - ms_played
# before comparison. The tolerance window applies AFTER normalization.
CROSS_SERVICE_TOLERANCE_SECONDS: Final = 30
CROSS_SERVICE_TOLERANCE_FALLBACK_SECONDS: Final = 180  # when ms_played unavailable
PREFERRED_SOURCE_ORDER: Final[tuple[str, ...]] = ("spotify", "lastfm")
END_TIME_SERVICES: Final[frozenset[str]] = frozenset({"spotify"})


@define(frozen=True, slots=True)
class PlayDeduplicationResult:
    """Result of cross-source play deduplication.

    Attributes:
        plays_to_insert: New plays that have no cross-source duplicate.
        plays_to_update: Existing plays that should be enriched with data from a
            matching new play. Each tuple is ``(existing_play_id, updated_fields)``.
        suppressed_plays: New plays suppressed because an existing cross-source
            duplicate was found and enriched instead.
        stats: Summary statistics for logging/reporting.
    """

    plays_to_insert: list[TrackPlay]
    plays_to_update: list[tuple[UUID, Mapping[str, JsonValue]]]
    suppressed_plays: list[TrackPlay]
    stats: dict[str, int] = field(factory=dict)


def _normalize_to_start_time(play: TrackPlay) -> float:
    """Convert a play's ``played_at`` to a start-time epoch for comparison.

    Spotify records end time; subtracting ``ms_played`` gives start time.
    Last.fm already records start time. Unknown services are treated as start time.

    Returns:
        Unix epoch (float seconds) of the estimated play start.
    """
    epoch = play.played_at.timestamp()

    if play.service in END_TIME_SERVICES and play.ms_played:
        return epoch - (play.ms_played / 1000.0)

    return epoch


def _get_tolerance(play_a: TrackPlay, play_b: TrackPlay) -> float:
    """Determine the appropriate tolerance window for two plays.

    Uses the tight tolerance when both plays have reliable timestamp normalization
    (i.e., the end-time service has ``ms_played``). Falls back to the wider
    tolerance when normalization quality is uncertain.
    """
    needs_fallback = False
    for p in (play_a, play_b):
        if p.service in END_TIME_SERVICES and not p.ms_played:
            needs_fallback = True
            break

    if needs_fallback:
        return float(CROSS_SERVICE_TOLERANCE_FALLBACK_SECONDS)
    return float(CROSS_SERVICE_TOLERANCE_SECONDS)


def _source_priority(service: str) -> int:
    """Lower number = higher priority (preferred source)."""
    order = PREFERRED_SOURCE_ORDER
    try:
        return order.index(service)
    except ValueError:
        return len(order)  # unknown services sort last


def _merge_context(winner: TrackPlay, loser: TrackPlay) -> dict[str, JsonValue] | None:
    """Merge the loser's context into the winner's, namespaced by service."""
    merged = dict(winner.context) if winner.context else {}

    if loser.context:
        merged[f"merged_from_{loser.service}"] = loser.context

    return merged or None


def _build_source_services(winner: TrackPlay, loser: TrackPlay) -> list[str]:
    """Build ordered source_services list from a matched pair."""
    existing = (
        list(winner.source_services) if winner.source_services else [winner.service]
    )
    if loser.service not in existing:
        existing.append(loser.service)
    return existing


def deduplicate_cross_source_plays(
    new_plays: list[TrackPlay],
    existing_plays: list[TrackPlay],
) -> PlayDeduplicationResult:
    """Identify and merge cross-source duplicate plays.

    For each new play, checks whether an existing play from a *different* service
    represents the same listening event (same track, close in time after
    timestamp normalization). When a match is found:

    - The higher-priority source wins (keeps core fields).
    - The loser's context is merged into the winner's under a namespaced key.
    - ``source_services`` is updated to record both sources.

    Only cross-service matches are considered — same-service duplicates are
    handled by the existing ``bulk_insert_plays`` deduplication.

    Args:
        new_plays: Plays about to be inserted (from the current import batch).
        existing_plays: Plays already in the database for the relevant time range.

    Returns:
        PlayDeduplicationResult with insert/update/suppress lists and stats.
    """
    plays_to_insert: list[TrackPlay] = []
    plays_to_update: list[tuple[UUID, Mapping[str, JsonValue]]] = []
    suppressed_plays: list[TrackPlay] = []
    stats: dict[str, int] = defaultdict(int)

    # Index existing plays by track_id for fast lookup
    existing_by_track: dict[UUID, list[TrackPlay]] = defaultdict(list)
    for ep in existing_plays:
        if ep.track_id is not None:
            existing_by_track[ep.track_id].append(ep)

    # Track which existing plays have already been matched (prevent double-matching)
    matched_existing_ids: set[UUID] = set()

    for new_play in new_plays:
        stats["total_new"] += 1

        if new_play.track_id is None:
            plays_to_insert.append(new_play)
            stats["no_track_id"] += 1
            continue

        candidates = existing_by_track.get(new_play.track_id, [])
        match_found = False

        new_start = _normalize_to_start_time(new_play)

        for existing in candidates:
            # Skip same-service (handled by bulk_insert dedup)
            if existing.service == new_play.service:
                continue

            # Skip already-matched
            if existing.id in matched_existing_ids:
                continue

            existing_start = _normalize_to_start_time(existing)
            time_diff = abs(new_start - existing_start)
            tolerance = _get_tolerance(new_play, existing)

            if time_diff <= tolerance:
                # Cross-source match found — merge
                match_found = True
                matched_existing_ids.add(existing.id)

                # Determine winner by source priority
                new_priority = _source_priority(new_play.service)
                existing_priority = _source_priority(existing.service)

                if new_priority < existing_priority:
                    # New play is higher priority — it becomes the insert,
                    # existing should be replaced (but we enrich new with existing context)
                    enriched = TrackPlay(
                        track_id=new_play.track_id,
                        service=new_play.service,
                        played_at=new_play.played_at,
                        ms_played=new_play.ms_played or existing.ms_played,
                        context=_merge_context(new_play, existing),
                        source_services=_build_source_services(new_play, existing),
                        import_timestamp=new_play.import_timestamp,
                        import_source=new_play.import_source,
                        import_batch_id=new_play.import_batch_id,
                    )
                    plays_to_insert.append(enriched)
                    suppressed_plays.append(new_play)
                    # Mark existing for update to add source_services
                    update: dict[str, JsonValue] = {
                        "source_services": _build_source_services(new_play, existing),
                    }
                    plays_to_update.append((existing.id, update))
                    stats["new_wins"] += 1
                else:
                    # Existing play is higher priority — enrich it, suppress new
                    merged_context = _merge_context(existing, new_play)
                    source_services = _build_source_services(existing, new_play)

                    update_fields: dict[str, JsonValue] = {
                        "source_services": source_services,
                    }
                    if merged_context != existing.context:
                        update_fields["context"] = merged_context
                    # Preserve ms_played if existing lacks it
                    if not existing.ms_played and new_play.ms_played:
                        update_fields["ms_played"] = new_play.ms_played
                    plays_to_update.append((existing.id, update_fields))

                    suppressed_plays.append(new_play)
                    stats["existing_wins"] += 1

                stats["cross_source_matches"] += 1
                break  # one match per new play

        if not match_found:
            plays_to_insert.append(new_play)
            stats["no_match"] += 1

    return PlayDeduplicationResult(
        plays_to_insert=plays_to_insert,
        plays_to_update=plays_to_update,
        suppressed_plays=suppressed_plays,
        stats=dict(stats),
    )


def compute_dedup_time_range(
    plays: list[TrackPlay],
) -> tuple[float, float] | None:
    """Compute the time range to query existing plays for dedup comparison.

    Expands the range by the fallback tolerance to catch edge-case matches
    at the boundaries.

    Returns:
        (start_epoch, end_epoch) or None if plays is empty.
    """
    if not plays:
        return None

    tolerance = timedelta(seconds=CROSS_SERVICE_TOLERANCE_FALLBACK_SECONDS)
    # Use raw played_at (not normalized) since we need to cover both
    # end-time and start-time semantics in the query range
    timestamps = [p.played_at for p in plays]
    if not timestamps:
        return None

    earliest = min(timestamps) - tolerance
    latest = max(timestamps) + tolerance

    return (earliest.timestamp(), latest.timestamp())
