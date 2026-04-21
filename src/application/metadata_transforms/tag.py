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
"""

from collections.abc import Sequence
from typing import Literal

from src.config import get_logger
from src.domain.entities.tag import normalize_tag
from src.domain.entities.track import Track, TrackList
from src.domain.transforms.core import Transform

logger = get_logger(__name__)

TagMatchMode = Literal["any", "all"]


def filter_by_tag(
    tags: Sequence[str],
    match_mode: TagMatchMode = "any",
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Filter tracks by tag presence.

    Args:
        tags: Tags to match (raw or normalized — normalized here before
            comparison).
        match_mode: ``"any"`` keeps tracks that have at least one matching
            tag; ``"all"`` requires every tag to be present on the track.
    """
    if not tags:
        raise ValueError("filter_by_tag: `tags` must be non-empty")

    target: frozenset[str] = frozenset(normalize_tag(t) for t in tags)

    def transform(t: TrackList) -> TrackList:
        tags_by_track = t.metadata.get("tags", {})

        def keep(track: Track) -> bool:
            track_tags = {tt.tag for tt in tags_by_track.get(track.id, ())}
            if match_mode == "all":
                return target.issubset(track_tags)
            return not track_tags.isdisjoint(target)

        kept = [track for track in t.tracks if keep(track)]
        logger.debug(
            "filter_by_tag applied",
            input_count=len(t.tracks),
            output_count=len(kept),
            match_mode=match_mode,
            tags=sorted(target),
        )
        return t.with_tracks(kept)

    return transform(tracklist) if tracklist is not None else transform


def filter_by_tag_namespace(
    namespace: str,
    values: Sequence[str] | None = None,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Filter tracks by tag namespace, optionally restricted to specific values.

    Args:
        namespace: Namespace to match (e.g. ``"mood"``).
        values: If non-empty, only keep tracks with a tag whose value is in
            this set (within the namespace). If ``None`` or empty, any tag
            in the namespace qualifies.
    """
    if not namespace:
        raise ValueError("filter_by_tag_namespace: `namespace` must be non-empty")

    value_set: frozenset[str] = frozenset(v.strip().lower() for v in values or ())

    def transform(t: TrackList) -> TrackList:
        tags_by_track = t.metadata.get("tags", {})

        def keep(track: Track) -> bool:
            for tt in tags_by_track.get(track.id, ()):
                if tt.namespace != namespace:
                    continue
                if not value_set or tt.value in value_set:
                    return True
            return False

        kept = [track for track in t.tracks if keep(track)]
        logger.debug(
            "filter_by_tag_namespace applied",
            input_count=len(t.tracks),
            output_count=len(kept),
            namespace=namespace,
            values=sorted(value_set),
        )
        return t.with_tracks(kept)

    return transform(tracklist) if tracklist is not None else transform
