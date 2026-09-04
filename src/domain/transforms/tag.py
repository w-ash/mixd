"""Tag-based transformations for track collections.

Reads from ``tracklist.metadata["tags"]`` (populated by ``enricher.tags``) —
shape: ``dict[UUID, list[TrackTag]]`` where untagged tracks are absent from
the dict.

Two filters:
- ``filter_by_tag(tags, match_mode)`` — tracks tagged with any / all of the
  specified tags. Input tags are normalized before matching, so callers can
  pass ``"mood:chill"`` or ``"Mood: Chill"`` interchangeably.
- ``filter_by_tag_namespace(namespace, values)`` — tracks tagged anywhere in
  a namespace, optionally restricted to specific values. ``values=None`` or
  empty means "any tag in this namespace."

Both filters normalize leniently: a value that cannot be a legal tag (bad
characters, too long) cannot equal any stored tag, so it matches nothing
instead of raising. The strict ``normalize_tag`` stays on the write path.

Purity: No side effects, logging, or external dependencies.
"""

from collections.abc import Sequence
from typing import Literal

from src.domain.entities.tag import normalize_tag
from src.domain.entities.track import Track, TrackList
from src.domain.transforms.core import Transform, dual_mode

TagMatchMode = Literal["any", "all"]


def _lenient_tag(raw: str) -> str | None:
    """Normalized tag, or ``None`` when ``raw`` can never equal a stored tag."""
    try:
        return normalize_tag(raw)
    except ValueError:
        return None


def filter_by_tag(
    tags: Sequence[str],
    match_mode: TagMatchMode = "any",
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Filter tracks by tag presence.

    Args:
        tags: Tags to match (raw or normalized — normalized here before
            comparison). A tag that cannot be legal matches nothing; under
            ``"all"`` one such tag makes the whole filter match nothing.
        match_mode: ``"any"`` keeps tracks that have at least one matching
            tag; ``"all"`` requires every tag to be present on the track.
    """
    if not tags:
        raise ValueError("filter_by_tag: `tags` must be non-empty")

    normalized = [_lenient_tag(t) for t in tags]
    target: frozenset[str] = frozenset(tag for tag in normalized if tag is not None)
    unsatisfiable = match_mode == "all" and None in normalized

    def transform(t: TrackList) -> TrackList:
        if unsatisfiable:
            return t.with_tracks([])
        tags_by_track = t.metadata.get("tags", {})

        def keep(track: Track) -> bool:
            track_tags = {tt.tag for tt in tags_by_track.get(track.id, ())}
            if match_mode == "all":
                return target.issubset(track_tags)
            return not track_tags.isdisjoint(target)

        kept = [track for track in t.tracks if keep(track)]
        return t.with_tracks(kept)

    return dual_mode(transform, tracklist)


def filter_by_tag_namespace(
    namespace: str,
    values: Sequence[str] | None = None,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Filter tracks by tag namespace, optionally restricted to specific values.

    Args:
        namespace: Namespace to match (e.g. ``"mood"``), case-insensitive.
        values: If non-empty, only keep tracks with a tag whose value is in
            this set (within the namespace). If ``None`` or empty, any tag
            in the namespace qualifies. Values that cannot be legal tags
            are dropped; if none survive, nothing matches.
    """
    if not namespace:
        raise ValueError("filter_by_tag_namespace: `namespace` must be non-empty")

    target_namespace = namespace.strip().lower()
    raw_values = list(values or ())
    value_set: frozenset[str] = frozenset(
        tag for tag in (_lenient_tag(v) for v in raw_values) if tag is not None
    )
    unsatisfiable = bool(raw_values) and not value_set

    def transform(t: TrackList) -> TrackList:
        if unsatisfiable:
            return t.with_tracks([])
        tags_by_track = t.metadata.get("tags", {})

        def keep(track: Track) -> bool:
            for tt in tags_by_track.get(track.id, ()):
                if tt.namespace != target_namespace:
                    continue
                if not value_set or tt.value in value_set:
                    return True
            return False

        kept = [track for track in t.tracks if keep(track)]
        return t.with_tracks(kept)

    return dual_mode(transform, tracklist)
