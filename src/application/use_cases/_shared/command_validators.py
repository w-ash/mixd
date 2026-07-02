"""Bespoke attrs field validators for Command classes.

Only contains validators with logic that attrs's built-in validators
(``attrs.validators.min_len``, ``in_``, ``ge``/``le``, ``optional``, ``and_``)
cannot express. Use the built-ins directly at the call site for everything else.
"""

from attrs import Attribute

from src.domain.entities.track import TrackList


def validate_tracklist_has_tracks(
    _instance: object, attribute: Attribute[TrackList], value: TrackList
) -> None:
    """Reject empty TrackList — destination commands need at least one track."""
    if not value.tracks:
        raise ValueError(f"{attribute.name} must contain tracks")


def non_empty_string[StrT: str](
    _instance: object, attribute: Attribute[StrT], value: StrT
) -> None:
    """Reject empty or whitespace-only strings.

    Differs from ``attrs.validators.min_len(1)`` by also rejecting strings
    that are non-empty but contain only whitespace (e.g., ``"   "``).

    Generic over ``StrT: str`` so it accepts ``NewType``-branded strings
    (e.g., ``ConnectorPlaylistIdentifier``) as well as plain ``str``.
    """
    if not value or not value.strip():
        raise ValueError(f"{attribute.name} must be a non-empty string, got: {value!r}")
