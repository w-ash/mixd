"""Preference-based transformations for track collections.

Reads from ``tracklist.metadata["preferences"]`` (populated by
``enricher.preferences``) — shape: ``dict[UUID, TrackPreference]`` where
unrated tracks are absent from the dict.

The filter supports include- and exclude-mode semantics:
- ``include=["star"]`` keeps only starred tracks.
- ``exclude=["nah"]`` removes nah'd tracks — **unrated tracks are kept**,
  because "exclude nah" means "remove nah'd", not "require a preference".

The sorter uses ``PREFERENCE_ORDER`` as the canonical strength ranking.
Unrated tracks sort to the bottom.
"""

from collections.abc import Sequence

from src.config import get_logger
from src.domain.entities.preference import (
    PREFERENCE_ORDER,
    PreferenceState,
)
from src.domain.entities.track import Track, TrackList
from src.domain.transforms.core import Transform

logger = get_logger(__name__)


def filter_by_preference(
    include: Sequence[PreferenceState] | None = None,
    exclude: Sequence[PreferenceState] | None = None,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Filter tracks by preference state.

    Exactly one of ``include`` or ``exclude`` should be provided:
    - ``include``: keep tracks whose preference state is in the set.
      Unrated tracks are **dropped** (they have no state to match).
    - ``exclude``: keep tracks whose preference state is NOT in the set.
      Unrated tracks are **kept** (there's no state to exclude).
    """
    if include and exclude:
        raise ValueError("filter_by_preference: pass include OR exclude, not both")
    if not include and not exclude:
        raise ValueError(
            "filter_by_preference: pass at least one of include or exclude"
        )

    include_set: frozenset[PreferenceState] = frozenset(include or ())
    exclude_set: frozenset[PreferenceState] = frozenset(exclude or ())

    def transform(t: TrackList) -> TrackList:
        preferences = t.metadata.get("preferences", {})

        def keep(track: Track) -> bool:
            pref = preferences.get(track.id)
            if include_set:
                return pref is not None and pref.state in include_set
            return pref is None or pref.state not in exclude_set

        kept = [track for track in t.tracks if keep(track)]
        logger.debug(
            "filter_by_preference applied",
            input_count=len(t.tracks),
            output_count=len(kept),
            include=sorted(include_set),
            exclude=sorted(exclude_set),
        )
        return t.with_tracks(kept)

    return transform(tracklist) if tracklist is not None else transform


def sort_by_preference(
    reverse: bool = True,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Sort tracks by preference strength.

    Uses ``PREFERENCE_ORDER`` as the ranking (star=3, yah=2, hmm=1, nah=0).
    Unrated tracks sort below all rated ones.

    Args:
        reverse: ``True`` (default) puts strongest preferences first
            (``star > yah > hmm > nah > unrated``). ``False`` reverses.
    """

    def transform(t: TrackList) -> TrackList:
        preferences = t.metadata.get("preferences", {})

        def sort_key(track: Track) -> tuple[int, int]:
            pref = preferences.get(track.id)
            # (has_preference, rank) so unrated tracks (0, 0) sort below
            # rated tracks regardless of direction, matching user intuition
            # that unrated is "no opinion" rather than "weaker than nah".
            if pref is None:
                return (0, 0)
            return (1, PREFERENCE_ORDER[pref.state])

        sorted_tracks = sorted(t.tracks, key=sort_key, reverse=reverse)
        logger.debug(
            "sort_by_preference applied",
            track_count=len(sorted_tracks),
            reverse=reverse,
        )
        return t.with_tracks(sorted_tracks)

    return transform(tracklist) if tracklist is not None else transform
