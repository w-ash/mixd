"""Deterministic projection of the play observation ledger.

Canonical plays are a pure function of the ``connector_plays`` observation
*set*: every source record is an immutable observation of a listening event,
and this module decides which observations describe the same event and which
field values survive. The grouping is associative, commutative, and
idempotent by construction — re-imports, arrival order, and batch boundaries
cannot change the result (the convergence guarantee v0.10.0 exists for; see
docs/backlog/play-import-convergence-findings.md).

Observation *channels* — ``(service, import_source)`` — are the unit of
grouping, not services: a Spotify GDPR export and the Spotify recently-played
API are two observers of the same listen, exactly like a Last.fm scrobble is.
``CHANNEL_SPECS`` is the multi-service seam: a future channel (Apple Music,
ListenBrainz) is one registry entry, no new merge code.

Timestamp semantics differ by channel (Spotify export stamps the END of a
play; Last.fm stamps the START) — comparison and the surviving ``played_at``
both use the normalized start time (findings §3: end - ms_played aligns
sources to ±5s for 80% of true pairs; unnormalized, 92% would miss).
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Final, Literal
from uuid import UUID

from attrs import define, field

from src.domain.entities import ConnectorTrackPlay
from src.domain.entities.operations import TrackContextFields
from src.domain.entities.shared import JsonValue
from src.domain.matching.text_normalization import normalize_for_comparison

# Cross-channel grouping tolerances (domain business rules; findings §3/§4).
# 30s catches 82.7% of true pairs after start-time normalization; the wider
# window is the fallback when normalization quality is uncertain (an end-time
# observation without ms_played) or a channel's semantics are uncalibrated.
CROSS_CHANNEL_TOLERANCE_SECONDS: Final = 30.0
CROSS_CHANNEL_TOLERANCE_FALLBACK_SECONDS: Final = 180.0

# An end-time observation's normalized start shifts back by at most this much.
# The projection service's chunk fetch margin equals this constant — the clamp
# is what turns "every group's members are visible to its owning chunk" from
# an assumption into an invariant (multi-hour ms_played values are real:
# sleep/ambient tracks in GDPR exports run 8h+; unclamped, such a row's group
# would be owned by no chunk and silently never projected).
MAX_NORMALIZED_START_SHIFT: Final = timedelta(hours=6)

# Spotify URI shape ("spotify:track:<22-char id>") — inlined here because the
# domain kernel cannot import config constants.
_SPOTIFY_URI_PARTS: Final = 3
_SPOTIFY_TRACK_ID_LENGTH: Final = 22


@define(frozen=True, slots=True)
class ChannelSpec:
    """Per-channel grouping behavior — the one place a channel is described.

    Attributes:
        name: Canonical channel name; used in ``merged_from_<name>`` context
            keys and stats.
        service: Service the channel observes (``TrackPlay.service`` value).
        import_source: Ledger ``import_source`` value the channel writes.
        priority: Survivorship rank — lower wins (richest data first).
        time_semantics: What ``played_at`` marks on this channel's records.
        timestamp_quality: Higher wins the surviving ``played_at`` — a channel
            that knows the true start (Last.fm) outranks one whose start is
            derived (export end - ms_played, pause-skewed; findings §3), and a
            channel that only approximates (Apple poll-window) ranks below
            every channel that knows.
        tolerance_override: Pairing tolerance forced by this channel (e.g.
            ``spotify_api`` stays at the wide fallback until its start-vs-end
            semantics are calibrated — v0.10.1).
    """

    name: str
    service: str
    import_source: str
    priority: int
    time_semantics: Literal["start", "end"]
    timestamp_quality: int
    tolerance_override: float | None = None


# Channel registry — priority order per findings §6:
# spotify_export > spotify_api > mixd > lastfm.
CHANNEL_SPECS: Final[Mapping[tuple[str, str], ChannelSpec]] = {
    ("spotify", "spotify_export"): ChannelSpec(
        name="spotify_export",
        service="spotify",
        import_source="spotify_export",
        priority=0,
        time_semantics="end",
        timestamp_quality=2,
    ),
    ("spotify", "spotify_api"): ChannelSpec(
        name="spotify_api",
        service="spotify",
        import_source="spotify_api",
        priority=1,
        time_semantics="start",
        timestamp_quality=1,
        # Uncalibrated start-vs-end semantics until the v0.10.1 first-poll
        # calibration — wide tolerance so pairing errs toward merging.
        tolerance_override=CROSS_CHANNEL_TOLERANCE_FALLBACK_SECONDS,
    ),
    ("mixd", "manual"): ChannelSpec(
        name="mixd",
        service="mixd",
        import_source="manual",
        priority=2,
        time_semantics="start",
        timestamp_quality=2,
    ),
    ("lastfm", "lastfm_api"): ChannelSpec(
        name="lastfm",
        service="lastfm",
        import_source="lastfm_api",
        priority=3,
        time_semantics="start",
        # Native second-precision true start — beats the export's derived
        # (pause-skewed) start for the surviving timestamp (findings §3).
        timestamp_quality=3,
    ),
}


class UnknownChannelError(ValueError):
    """An observation's (service, import_source) has no registered ChannelSpec.

    Fails loud by design: a new channel must register its spec (that registry
    entry IS the multi-service seam) — silently defaulting would mis-rank its
    observations in every merge.
    """


def channel_for(entry: ConnectorTrackPlay) -> ChannelSpec:
    """Resolve an observation's channel spec, raising on unregistered channels."""
    key = (entry.service, entry.import_source or "")
    spec = CHANNEL_SPECS.get(key)
    if spec is None:
        raise UnknownChannelError(
            f"No ChannelSpec registered for {key!r} — add it to "
            f"play_projection.CHANNEL_SPECS"
        )
    return spec


def normalized_start_time(entry: ConnectorTrackPlay, spec: ChannelSpec) -> datetime:
    """An observation's played_at normalized to the START of the play.

    End-time channels subtract ``ms_played`` (clamped to
    ``MAX_NORMALIZED_START_SHIFT`` so the shift can never exceed the chunk
    fetch margin); without it the raw timestamp stands (and pairing widens to
    the fallback tolerance).
    """
    if spec.time_semantics == "end" and entry.ms_played:
        shift = min(timedelta(milliseconds=entry.ms_played), MAX_NORMALIZED_START_SHIFT)
        return entry.played_at - shift
    return entry.played_at


def _pair_tolerance(
    a: ConnectorTrackPlay,
    a_spec: ChannelSpec,
    b: ConnectorTrackPlay,
    b_spec: ChannelSpec,
) -> float:
    """Pairing tolerance for two observations (seconds)."""
    overrides = [
        spec.tolerance_override
        for spec in (a_spec, b_spec)
        if spec.tolerance_override is not None
    ]
    if overrides:
        return max(overrides)
    for entry, spec in ((a, a_spec), (b, b_spec)):
        if spec.time_semantics == "end" and not entry.ms_played:
            return CROSS_CHANNEL_TOLERANCE_FALLBACK_SECONDS
    return CROSS_CHANNEL_TOLERANCE_SECONDS


def bridge_key(entry: ConnectorTrackPlay) -> str:
    """Exact-normalized identity key used to bridge divergent resolutions.

    All observed cross-source resolution divergence is casing/punctuation
    (findings §5b) — an exact normalized artist::title bridge captures it
    entirely; no fuzzy escalation (§8.3).
    """
    return (
        f"{normalize_for_comparison(entry.artist_name)}"
        f"::{normalize_for_comparison(entry.track_name)}"
    )


@define(frozen=True, slots=True)
class PlayGroup:
    """One listening event: its observing members plus absorbed duplicates.

    ``members`` are the per-channel representatives (at most one per channel —
    the grouping invariant) sorted by (priority, id); survivorship reads them.
    ``absorbed`` are same-channel duplicate observations collapsed into a
    member (identical (channel, played_at, identifier), differing ms_played —
    findings §7); they contribute ledger membership but never field values.
    """

    members: tuple[ConnectorTrackPlay, ...]
    absorbed: tuple[ConnectorTrackPlay, ...] = ()

    @property
    def member_ids(self) -> tuple[UUID, ...]:
        """Every ledger observation this event covers (members + absorbed)."""
        return tuple(e.id for e in (*self.members, *self.absorbed))

    @property
    def divergent(self) -> bool:
        """True when members resolved to more than one canonical track —
        the identity-layer defect the bridge papers over at the play layer."""
        return len({e.resolved_track_id for e in self.members}) > 1


@define(frozen=True, slots=True)
class ProjectedPlay:
    """Survivorship output for one group — the canonical play's field values."""

    track_id: UUID
    service: str
    played_at: datetime
    user_id: str
    ms_played: int | None
    context: Mapping[str, JsonValue] | None
    source_services: tuple[str, ...]
    import_timestamp: datetime | None
    import_source: str | None
    import_batch_id: str | None
    member_ids: tuple[UUID, ...]
    divergent: bool


@define(frozen=True, slots=True)
class ProjectionResult:
    """Groups + merged plays + convergence stats for a set of observations."""

    groups: list[PlayGroup]
    plays: list[ProjectedPlay]
    stats: dict[str, int] = field(factory=dict)


class _UnionFind:
    """Disjoint sets over entry indices, tracking each root's channel names.

    The channel-set gate enforces the grouping invariant: a listening event is
    observed at most once per channel, so two components may union only when
    their channel sets are disjoint. This is what keeps two skip-restarts on
    the same channel distinct even through cross-channel chains.
    """

    def __init__(self, channel_names: Sequence[str]) -> None:
        self._parent = list(range(len(channel_names)))
        self._channels: list[set[str]] = [{name} for name in channel_names]

    def find(self, i: int) -> int:
        root = i
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[i] != root:  # path compression
            self._parent[i], i = root, self._parent[i]
        return root

    def try_union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb or self._channels[ra] & self._channels[rb]:
            return False
        self._parent[rb] = ra
        self._channels[ra] |= self._channels[rb]
        self._channels[rb] = set()
        return True


def _collapse_same_channel(
    entries: Sequence[ConnectorTrackPlay],
) -> tuple[list[ConnectorTrackPlay], dict[UUID, list[ConnectorTrackPlay]], int]:
    """Collapse same-channel observations of one listen into a representative.

    Identical (user, channel, played_at, identifier) with differing ms_played
    are ONE observation (findings §7: 266 such pairs inside the GDPR export
    defeat the ON CONFLICT key). The max-ms_played record represents; ties
    break on lowest id. Returns (representatives, absorbed-by-representative,
    collapsed-count).
    """
    by_observation: dict[tuple[str, str, datetime, str], list[ConnectorTrackPlay]] = (
        defaultdict(list)
    )
    for entry in entries:
        spec = channel_for(entry)
        by_observation[
            entry.user_id, spec.name, entry.played_at, entry.connector_track_identifier
        ].append(entry)

    representatives: list[ConnectorTrackPlay] = []
    absorbed: dict[UUID, list[ConnectorTrackPlay]] = {}
    collapsed = 0
    for group in by_observation.values():
        group.sort(key=lambda e: (-(e.ms_played or 0), e.id))
        representative, *rest = group
        representatives.append(representative)
        if rest:
            absorbed[representative.id] = rest
            collapsed += len(rest)
    # Deterministic order regardless of input permutation.
    representatives.sort(key=lambda e: (e.played_at, e.id))
    return representatives, absorbed, collapsed


def group_ledger_entries(
    entries: Sequence[ConnectorTrackPlay],
) -> tuple[list[PlayGroup], dict[str, int]]:
    """Group resolved ledger observations into listening events.

    Deterministic in the entry *set* — any permutation or batch partition of
    the same observations yields identical groups. Steps:

    1. Same-channel collapse (identical (channel, ts, identifier) → one
       observation, max ms_played).
    2. Candidate pairs: cross-channel observations that resolved to the same
       canonical track — or whose exact-normalized artist::title matches (the
       resolution-divergence bridge, findings §5b) — within the pair's
       tolerance of each other on normalized start time.
    3. Greedy nearest-first one-to-one assignment: pairs sorted by
       (|Δstart|, channel priority, id); a union only happens when the two
       components share no channel (one observation per channel per event).

    Entries must all belong to one user and be resolved
    (``resolved_track_id`` set) — the caller's fetch guarantees both.
    """
    if not entries:
        return [], {"same_channel_collapsed": 0, "resolution_divergence": 0}

    reps, absorbed, collapsed = _collapse_same_channel(entries)
    specs = [channel_for(e) for e in reps]
    starts = list(map(normalized_start_time, reps, specs, strict=True))

    max_window = max(
        CROSS_CHANNEL_TOLERANCE_FALLBACK_SECONDS,
        max(
            (
                spec.tolerance_override
                for spec in CHANNEL_SPECS.values()
                if spec.tolerance_override is not None
            ),
            default=0.0,
        ),
    )

    # Enumerate candidate pairs via a sliding window over start-sorted entries.
    order = sorted(range(len(reps)), key=lambda i: (starts[i], reps[i].id))
    candidates: list[tuple[float, int, int, UUID, UUID, int, int]] = []
    for pos, i in enumerate(order):
        for nxt in range(pos + 1, len(order)):
            j = order[nxt]
            delta = (starts[j] - starts[i]).total_seconds()
            if delta > max_window:
                break
            a, b = reps[i], reps[j]
            if a.user_id != b.user_id or specs[i].name == specs[j].name:
                continue
            same_track = a.resolved_track_id == b.resolved_track_id
            if not same_track and bridge_key(a) != bridge_key(b):
                continue
            if abs(delta) > _pair_tolerance(a, specs[i], b, specs[j]):
                continue
            first, second = sorted(
                (i, j), key=lambda k: (specs[k].priority, reps[k].id)
            )
            candidates.append((
                abs(delta),
                specs[first].priority,
                specs[second].priority,
                reps[first].id,
                reps[second].id,
                first,
                second,
            ))

    # Plain tuple sort: (|Δ|, priorities, ids) lead the tuple and are unique
    # per pair, so the trailing indices never influence the order.
    uf = _UnionFind([spec.name for spec in specs])
    for *_sort_key, first, second in sorted(candidates):
        _ = uf.try_union(first, second)

    components: dict[int, list[int]] = defaultdict(list)
    for i in range(len(reps)):
        components[uf.find(i)].append(i)

    groups: list[PlayGroup] = []
    divergence = 0
    for indices in components.values():
        members = tuple(
            sorted(
                (reps[i] for i in indices),
                key=lambda e: (channel_for(e).priority, e.id),
            )
        )
        group = PlayGroup(
            members=members,
            absorbed=tuple(
                sibling for m in members for sibling in absorbed.get(m.id, ())
            ),
        )
        if group.divergent:
            divergence += 1
        groups.append(group)

    # Deterministic output order: by surviving start time, then winner id.
    groups.sort(
        key=lambda g: (
            normalized_start_time(g.members[0], channel_for(g.members[0])),
            g.members[0].id,
        )
    )
    return groups, {
        "same_channel_collapsed": collapsed,
        "resolution_divergence": divergence,
    }


def merge_group(group: PlayGroup) -> ProjectedPlay:
    """Per-field survivorship over a group's members (findings §6).

    Richest data wins per *attribute*, not per record: the winner (lowest
    channel priority, then lowest id — uuid7 ids are time-ordered, so the
    tiebreak is deterministic) supplies identity and provenance fields;
    ``played_at`` comes from the best ``timestamp_quality`` member's
    normalized start; ``ms_played`` is the first non-null by priority; each
    losing member's context nests under ``merged_from_<channel>`` (today's
    persisted shape).
    """
    members = group.members
    winner = members[0]
    timestamp_member = min(
        members,
        key=lambda e: (
            -channel_for(e).timestamp_quality,
            channel_for(e).priority,
            e.id,
        ),
    )
    played_at = normalized_start_time(timestamp_member, channel_for(timestamp_member))

    ms_played = next((e.ms_played for e in members if e.ms_played is not None), None)

    source_services: list[str] = []
    for entry in members:
        if entry.service not in source_services:
            source_services.append(entry.service)

    context: dict[str, JsonValue] = dict(build_play_context(winner))
    for loser in members[1:]:
        context[f"merged_from_{channel_for(loser).name}"] = build_play_context(loser)

    if winner.resolved_track_id is None:  # caller fetches resolved rows only
        raise ValueError(f"Unresolved observation {winner.id} cannot be projected")

    return ProjectedPlay(
        track_id=winner.resolved_track_id,
        service=winner.service,
        played_at=played_at,
        user_id=winner.user_id,
        ms_played=ms_played,
        context=context or None,
        source_services=tuple(source_services),
        import_timestamp=winner.import_timestamp,
        import_source=winner.import_source,
        import_batch_id=winner.import_batch_id,
        member_ids=group.member_ids,
        divergent=group.divergent,
    )


def project_ledger_entries(
    entries: Sequence[ConnectorTrackPlay],
) -> ProjectionResult:
    """Group + merge in one pass — the pipeline/rebuild entry point."""
    groups, stats = group_ledger_entries(entries)
    return ProjectionResult(
        groups=groups,
        plays=[merge_group(g) for g in groups],
        stats=stats,
    )


# --------------------------------------------------------------------------- #
# Play context builders — pure functions of one observation.                  #
#                                                                             #
# Ported byte-identically (key set) from the connector play resolvers so the  #
# projection can rebuild canonical context from the ledger alone — the        #
# rebuild command has no resolver in the loop. The key set is persisted into  #
# track_plays.context; changing it is user-visible data drift.                #
# --------------------------------------------------------------------------- #

_LASTFM_KNOWN_KEYS: Final = (
    "lastfm_track_url",
    "lastfm_artist_url",
    "lastfm_album_url",
    "mbid",
    "artist_mbid",
    "album_mbid",
    "streamable",
    "loved",
)

_SPOTIFY_KNOWN_KEYS: Final = (
    TrackContextFields.PLATFORM,
    TrackContextFields.COUNTRY,
    TrackContextFields.REASON_START,
    TrackContextFields.REASON_END,
    TrackContextFields.SHUFFLE,
    "skipped",
    TrackContextFields.OFFLINE,
    TrackContextFields.INCOGNITO_MODE,
    "track_uri",
)

# Matches the resolvers' persisted marker; kept for context-shape continuity.
_ARCHITECTURE_VERSION: Final = "connector_plays_deferred_resolution"
# The spotify resolver's per-run resolution method (direct/redirect/fallback)
# is not reconstructible from the ledger; the projection records the stable
# resolver marker instead (MatchMethod.PLAY_RESOLVER's value).
_SPOTIFY_RESOLUTION_METHOD: Final = "spotify_connector_play_resolver"
_LASTFM_RESOLUTION_METHOD: Final = "lastfm_connector_play_resolver"


def spotify_id_from_uri(spotify_uri: str) -> str | None:
    """Extract the track id from a ``spotify:track:<id>`` URI, else None.

    The single implementation — the Spotify resolver delegates here so
    import-time and rebuild-time context derive identical ids.
    """
    parts = spotify_uri.split(":")
    if len(parts) != _SPOTIFY_URI_PARTS or parts[0] != "spotify" or parts[1] != "track":
        return None
    track_id = parts[2]
    if (
        len(track_id) == _SPOTIFY_TRACK_ID_LENGTH
        and track_id.replace("_", "a").replace("-", "a").isalnum()
    ):
        return track_id
    return None


def _passthrough(
    metadata: Mapping[str, JsonValue], known: Iterable[str]
) -> dict[str, JsonValue]:
    known_set = set(known)
    return {k: v for k, v in metadata.items() if k not in known_set}


def _lastfm_context(entry: ConnectorTrackPlay) -> dict[str, JsonValue]:
    md = entry.service_metadata
    return {
        "track_name": entry.track_name,
        "artist_name": entry.artist_name,
        "album_name": entry.album_name,
        "lastfm_track_url": md.get("lastfm_track_url"),
        "lastfm_artist_url": md.get("lastfm_artist_url"),
        "lastfm_album_url": md.get("lastfm_album_url"),
        "mbid": md.get("mbid"),
        "artist_mbid": md.get("artist_mbid"),
        "album_mbid": md.get("album_mbid"),
        "streamable": md.get("streamable"),
        "loved": md.get("loved"),
        "resolution_method": _LASTFM_RESOLUTION_METHOD,
        "architecture_version": _ARCHITECTURE_VERSION,
        **_passthrough(md, _LASTFM_KNOWN_KEYS),
    }


def _spotify_context(entry: ConnectorTrackPlay) -> dict[str, JsonValue]:
    md = entry.service_metadata
    track_uri = md.get("track_uri")
    spotify_id = None
    if isinstance(track_uri, str):
        spotify_id = spotify_id_from_uri(track_uri)
    if spotify_id is None and entry.connector_track_identifier.startswith(
        "spotify:track:"
    ):
        spotify_id = spotify_id_from_uri(entry.connector_track_identifier)
    return {
        TrackContextFields.TRACK_NAME: entry.track_name,
        TrackContextFields.ARTIST_NAME: entry.artist_name,
        TrackContextFields.ALBUM_NAME: entry.album_name,
        TrackContextFields.PLATFORM: md.get("platform"),
        TrackContextFields.COUNTRY: md.get("country"),
        TrackContextFields.REASON_START: md.get("reason_start"),
        TrackContextFields.REASON_END: md.get("reason_end"),
        TrackContextFields.SHUFFLE: md.get("shuffle"),
        "skipped": md.get("skipped"),
        TrackContextFields.OFFLINE: md.get("offline"),
        TrackContextFields.INCOGNITO_MODE: md.get("incognito_mode", False),
        TrackContextFields.SPOTIFY_TRACK_URI: md.get("track_uri"),
        "spotify_track_id": spotify_id,
        "resolution_method": _SPOTIFY_RESOLUTION_METHOD,
        "architecture_version": _ARCHITECTURE_VERSION,
        **_passthrough(md, _SPOTIFY_KNOWN_KEYS),
    }


def _generic_context(entry: ConnectorTrackPlay) -> dict[str, JsonValue]:
    return {
        "track_name": entry.track_name,
        "artist_name": entry.artist_name,
        "album_name": entry.album_name,
        "resolution_method": "play_projection",
        "architecture_version": _ARCHITECTURE_VERSION,
        **dict(entry.service_metadata),
    }


def build_play_context(entry: ConnectorTrackPlay) -> dict[str, JsonValue]:
    """The persisted play context for one observation, keyed by service shape."""
    if entry.service == "lastfm":
        return _lastfm_context(entry)
    if entry.service == "spotify":
        return _spotify_context(entry)
    return _generic_context(entry)
