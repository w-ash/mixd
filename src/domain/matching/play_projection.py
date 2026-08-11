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

Timestamp semantics differ by channel (both Spotify channels stamp the END of
a play; Last.fm stamps the START) — comparison and the surviving ``played_at``
both use the normalized start time (findings §3: end - ms_played aligns
sources to ±5s for 80% of true pairs; unnormalized, 92% would miss). How far
back an end stamp sits from its start is *ledger data*, never a per-channel
branch: an observation carries its own listened ``ms_played`` or, when its
channel cannot observe one, a shift its importer derived
(:data:`START_SHIFT_MS_KEY`).
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
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

# Ledger key naming how far back an observation's ``played_at`` sits from the
# start of the play, in milliseconds. It is the seam for an end-stamping
# channel that cannot observe listened time: the importer derives the shift
# from its payload and writes it as data, so the projection needs no branch for
# that channel (v0.10.3 C1 — the Spotify recently-played API stamps the END and
# reports no ``ms_played``, leaving every observation a track-length adrift).
# Never a stand-in for ``ms_played`` itself: survivorship takes the first
# non-null ``ms_played``, so a derived duration written there would corrupt
# listening-time statistics the moment it won a merge.
START_SHIFT_MS_KEY: Final = "start_shift_ms"

# Same-channel duplicate window (v0.10.3 C2). Spotify's export emits a play
# twice with timestamps 1-3s apart and byte-identical ``ms_played`` — 435 such
# pairs in the reference corpus, invisible to both the ledger's ON CONFLICT key
# and the exact-instant collapse because ``played_at`` differs. Sized from the
# observed jitter band; the *safety* argument is the ``ms_played`` bound in
# :func:`_one_observation` rather than this constant.
SAME_CHANNEL_JITTER_SECONDS: Final = 3.0

_MS_PER_SECOND: Final = 1000

# Spotify URI shape ("spotify:track:<22-char id>") — inlined here because the
# domain kernel cannot import config constants.
_SPOTIFY_URI_PARTS: Final = 3
_SPOTIFY_TRACK_ID_LENGTH: Final = 22


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

# The recently-played API's own metadata shape — a different set from the
# export's, which is why the two Spotify channels need separate builders.
_SPOTIFY_API_KNOWN_KEYS: Final = (
    "track_uri",
    "duration_ms",
    "context_type",
    "context_uri",
    START_SHIFT_MS_KEY,
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


def _spotify_api_context(entry: ConnectorTrackPlay) -> dict[str, JsonValue]:
    """Context for a recently-played API observation.

    Deliberately NOT ``_spotify_context``: the API reports none of the export's
    behavioral fields (platform, country, reason_start/end, shuffle, skipped,
    offline, incognito), so reusing that builder would persist a context of
    nulls that reads as "we observed these and they were empty". What the API
    does uniquely observe is playback *context* — the playlist or album the
    play came from.
    """
    md = entry.service_metadata
    track_uri = md.get("track_uri")
    spotify_id = spotify_id_from_uri(track_uri) if isinstance(track_uri, str) else None
    return {
        TrackContextFields.TRACK_NAME: entry.track_name,
        TrackContextFields.ARTIST_NAME: entry.artist_name,
        TrackContextFields.ALBUM_NAME: entry.album_name,
        TrackContextFields.SPOTIFY_TRACK_URI: track_uri,
        "spotify_track_id": spotify_id,
        "context_type": md.get("context_type"),
        "context_uri": md.get("context_uri"),
        # The track's length, which is what the API observes in place of a
        # listened duration; never a stand-in for ms_played, which stays null
        # on this channel. Its derived END→START shift is ledger data
        # (START_SHIFT_MS_KEY), read by normalization rather than rendered here.
        "duration_ms": md.get("duration_ms"),
        "resolution_method": _SPOTIFY_RESOLUTION_METHOD,
        "architecture_version": _ARCHITECTURE_VERSION,
        **_passthrough(md, _SPOTIFY_API_KNOWN_KEYS),
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
        context_builder: Builds the persisted play context for one of this
            channel's observations. Lives here rather than in a service-keyed
            if-chain because two channels of the same service can observe
            different things — the Spotify export carries behavioral fields
            (shuffle, skip reasons) the recently-played API never sees, so a
            per-service builder would hand API plays an export-shaped context
            full of nulls.
        tolerance_override: Pairing tolerance forced by this channel, for one
            whose timestamp semantics are declared but not yet calibrated. No
            channel sets it today; it is the seam a newly registered one uses
            to err toward merging until its own calibration lands.
    """

    name: str
    service: str
    import_source: str
    priority: int
    time_semantics: Literal["start", "end"]
    timestamp_quality: int
    context_builder: Callable[[ConnectorTrackPlay], dict[str, JsonValue]]
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
        context_builder=_spotify_context,
    ),
    ("spotify", "spotify_api"): ChannelSpec(
        name="spotify_api",
        service="spotify",
        import_source="spotify_api",
        priority=1,
        # Calibrated 2026-08 (v0.10.3 C1): median lastfm-minus-api offset
        # -205.8s against a 219.7s median track length, 90/90 nearest pairs
        # one-directional. The channel reports no ms_played, so its shift back
        # to the start rides on START_SHIFT_MS_KEY; an observation without one
        # (a partial play, or a row written before calibration) normalizes to
        # its raw stamp and pairs at the wide fallback tolerance.
        time_semantics="end",
        # Below the export's: that channel's start is derived from an *observed*
        # listened duration, this one's from the track's nominal length.
        timestamp_quality=1,
        context_builder=_spotify_api_context,
    ),
    ("mixd", "manual"): ChannelSpec(
        name="mixd",
        service="mixd",
        import_source="manual",
        priority=2,
        time_semantics="start",
        timestamp_quality=2,
        context_builder=_generic_context,
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
        context_builder=_lastfm_context,
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


def start_shift(entry: ConnectorTrackPlay, spec: ChannelSpec) -> timedelta | None:
    """How far back this observation's ``played_at`` sits from the play's start.

    Zero on a start-stamping channel. On an end-stamping one it is the
    observation's own listened ``ms_played`` — or, when its channel cannot
    observe one, the shift its importer derived and wrote to the ledger
    (:data:`START_SHIFT_MS_KEY`). Observed beats derived: a listened duration
    is what actually elapsed, a derived shift only what could have.

    ``None`` means "this end stamp cannot be normalized" — the caller keeps the
    raw timestamp and widens pairing to the fallback tolerance. Clamped to
    ``MAX_NORMALIZED_START_SHIFT`` so the shift can never exceed the chunk fetch
    margin.
    """
    if spec.time_semantics == "start":
        return timedelta(0)
    shift_ms = entry.ms_played or _derived_shift_ms(entry)
    if not shift_ms:
        return None
    return min(timedelta(milliseconds=shift_ms), MAX_NORMALIZED_START_SHIFT)


def _derived_shift_ms(entry: ConnectorTrackPlay) -> int | None:
    """The importer-derived END→START shift on this observation, if it has one."""
    value = entry.service_metadata.get(START_SHIFT_MS_KEY)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def normalized_start_time(entry: ConnectorTrackPlay, spec: ChannelSpec) -> datetime:
    """An observation's played_at normalized to the START of the play."""
    return entry.played_at - (start_shift(entry, spec) or timedelta(0))


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
    # An end stamp that could not be normalized still sits a play-length from
    # its start, so the pair only gets the tight window when both sides were
    # normalizable.
    for entry, spec in ((a, a_spec), (b, b_spec)):
        if start_shift(entry, spec) is None:
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
    member (:func:`_one_observation`); they contribute ledger membership but
    never field values.
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


def _one_observation(a: ConnectorTrackPlay, b: ConnectorTrackPlay) -> bool:
    """Are these two records of one track on one channel the same observation?

    Two rules, unioned:

    * **Same instant** — identical ``played_at``, whatever ``ms_played`` says
      (findings §7: 266 such pairs inside the GDPR export defeat the ledger's
      ON CONFLICT key, which includes ``ms_played``).
    * **Jittered** — byte-identical ``ms_played`` a few seconds apart (v0.10.3
      C2). Spotify's export writes these upstream; the ledger sees two rows and
      counts the listen twice.

    The second rule cannot swallow a genuine repeat: two real plays of one
    track run back to back, so their end stamps sit at least the second play's
    listened duration apart — and with the two durations identical, that is
    ``ms_played`` itself. Bounding the window by ``ms_played`` therefore makes
    a genuine pair unreachable by arithmetic rather than by luck, and keeps the
    rule off channels that report no listened time at all.

    It also cannot swallow the segments of an interrupted listen: those differ
    in ``ms_played``, which is exactly what distinguishes them (v0.10.3 C9).
    """
    delta = abs((a.played_at - b.played_at).total_seconds())
    if delta == 0:
        return True
    if a.ms_played != b.ms_played or not a.ms_played:
        return False
    return delta <= min(SAME_CHANNEL_JITTER_SECONDS, a.ms_played / _MS_PER_SECOND)


def _duplicate_clusters(
    bucket: Sequence[ConnectorTrackPlay],
) -> list[list[ConnectorTrackPlay]]:
    """Partition one (user, channel, track) bucket into single observations.

    ``bucket`` is ordered by (played_at, id), so the forward scan can stop at
    the jitter window. The partition is the transitive closure of
    :func:`_one_observation` — an equivalence relation, hence independent of
    input order, which is what keeps the collapse convergent.
    """
    parent = list(range(len(bucket)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, left in enumerate(bucket):
        for j in range(i + 1, len(bucket)):
            right = bucket[j]
            if (
                right.played_at - left.played_at
            ).total_seconds() > SAME_CHANNEL_JITTER_SECONDS:
                break
            if _one_observation(left, right):
                roots = (find(i), find(j))
                parent[max(roots)] = min(roots)

    clusters: dict[int, list[ConnectorTrackPlay]] = defaultdict(list)
    for i, entry in enumerate(bucket):
        clusters[find(i)].append(entry)
    return list(clusters.values())


def _collapse_same_channel(
    entries: Sequence[ConnectorTrackPlay],
) -> tuple[list[ConnectorTrackPlay], dict[UUID, list[ConnectorTrackPlay]], int]:
    """Collapse same-channel observations of one listen into a representative.

    See :func:`_one_observation` for what counts as one observation. The
    max-ms_played record represents (it saw the most of the play); ties break
    on the earliest stamp, then lowest id. Returns (representatives,
    absorbed-by-representative, collapsed-count).
    """
    buckets: dict[tuple[str, str, str], list[ConnectorTrackPlay]] = defaultdict(list)
    for entry in entries:
        spec = channel_for(entry)
        buckets[entry.user_id, spec.name, entry.connector_track_identifier].append(
            entry
        )

    representatives: list[ConnectorTrackPlay] = []
    absorbed: dict[UUID, list[ConnectorTrackPlay]] = {}
    collapsed = 0
    for bucket in buckets.values():
        bucket.sort(key=lambda e: (e.played_at, e.id))
        for cluster in _duplicate_clusters(bucket):
            cluster.sort(key=lambda e: (-(e.ms_played or 0), e.played_at, e.id))
            representative, *rest = cluster
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

    1. Same-channel collapse (one channel's duplicate records of one track →
       one observation, max ms_played — :func:`_one_observation`).
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


# Fallback when a row names no channel. Keyed on service alone — the pre-channel
# behaviour, kept because ``import_source`` is nullable and rows written before it
# was populated would otherwise lose every service-specific key (mbid, the
# Last.fm URLs, spotify_track_id) to the generic builder. The export builder is
# the right default for Spotify: it is the shape those legacy rows were written in.
_FALLBACK_CONTEXT_BUILDERS: Final[
    Mapping[str, Callable[[ConnectorTrackPlay], dict[str, JsonValue]]]
] = {
    "lastfm": _lastfm_context,
    "spotify": _spotify_context,
}


def build_play_context(entry: ConnectorTrackPlay) -> dict[str, JsonValue]:
    """The persisted play context for one observation, keyed by its channel.

    Unlike :func:`channel_for`, an unrecognised ``(service, import_source)`` does
    not raise: grouping must fail loud on an unranked channel, but a context is
    still recoverable without one. It falls back to the service-level builder
    (and only then to the generic one), so a row with a null or unknown
    ``import_source`` keeps its service-specific keys instead of silently
    dropping to a bare name/artist/album context.
    """
    spec = CHANNEL_SPECS.get((entry.service, entry.import_source or ""))
    if spec is not None:
        return spec.context_builder(entry)
    fallback = _FALLBACK_CONTEXT_BUILDERS.get(entry.service)
    return fallback(entry) if fallback is not None else _generic_context(entry)
