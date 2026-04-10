"""Bespoke attrs field validators for Command classes.

Only contains validators with logic that attrs's built-in validators
(``attrs.validators.min_len``, ``in_``, ``ge``/``le``, ``optional``, ``and_``)
cannot express. Use the built-ins directly at the call site for everything else.
"""

from collections.abc import Callable
from typing import cast

from attrs import Attribute

from src.domain.entities.track import TrackList


def validate_tracklist_has_tracks(
    _instance: object, attribute: Attribute[TrackList], value: TrackList
) -> None:
    """Reject empty TrackList — destination commands need at least one track."""
    if not value.tracks:
        raise ValueError(f"{attribute.name} must contain tracks")


def non_empty_string(_instance: object, attribute: Attribute[str], value: str) -> None:
    """Reject empty or whitespace-only strings.

    Differs from ``attrs.validators.min_len(1)`` by also rejecting strings
    that are non-empty but contain only whitespace (e.g., ``"   "``).
    """
    if not value or not value.strip():
        raise ValueError(f"{attribute.name} must be a non-empty string, got: {value!r}")


def api_batch_size_validator(
    max_batch_size_setting: str = "api.spotify_large_batch_size",
) -> Callable[[object, Attribute[int], int], None]:
    """Validator factory: int field bounded by a runtime config value.

    The max comes from settings (loaded at runtime), so this can't be expressed
    with ``attrs.validators.le(...)`` which requires a static bound.
    """

    def validator(_instance: object, attribute: Attribute[int], value: int) -> None:
        from src.config import settings

        setting_parts = max_batch_size_setting.split(".")
        max_value: object = settings
        for part in setting_parts:
            max_value = cast(object, getattr(max_value, part))

        if not isinstance(max_value, int):
            raise TypeError(
                f"Expected int for settings.{max_batch_size_setting}, got {type(max_value).__name__}"
            )
        if value > max_value:
            raise ValueError(
                f"{attribute.name} cannot exceed {max_value} (from settings.{max_batch_size_setting}), got {value}"
            )

    return validator
