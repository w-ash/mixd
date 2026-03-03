"""Attrs field validators for command classes.

Provides reusable attrs validators for command field validation using Python 3.13+
patterns. These validators execute at construction time for fail-fast behavior.

Example usage:
    ```python
    from attrs import define, field
    from src.application.use_cases._shared.command_validators import (
        non_empty_string,
        positive_int_in_range,
    )


    @define(frozen=True, slots=True)
    class CreatePlaylistCommand:
        name: str = field(validator=non_empty_string)
        limit: int = field(validator=positive_int_in_range(1, 10000))
    ```
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: attrs Attribute[Any] validator protocol signatures

from typing import Any

from attrs import Attribute

from src.config.constants import BusinessLimits
from src.domain.entities.track import TrackList


def validate_tracklist_has_tracks(
    _instance: object, attribute: Attribute[TrackList], value: TrackList
) -> None:
    """Validates that TrackList contains tracks.

    Args:
        _instance: The attrs instance being validated
        attribute: The field attribute being validated
        value: The TrackList value to validate

    Raises:
        ValueError: If the TrackList has no tracks
    """
    if not value.tracks:
        raise ValueError(f"{attribute.name} must contain tracks")


def non_empty_string(_instance: Any, attribute: Attribute[Any], value: str) -> None:
    """Validates that a string field is non-empty after stripping whitespace.

    Args:
        instance: The attrs instance being validated
        attribute: The field attribute being validated
        value: The string value to validate

    Raises:
        ValueError: If the string is empty or contains only whitespace
    """
    if not value or not value.strip():
        raise ValueError(f"{attribute.name} must be a non-empty string, got: {value!r}")


def positive_int_in_range(
    min_value: int = 1, max_value: int = BusinessLimits.MAX_USER_LIMIT
) -> Any:
    """Creates a validator for positive integers within a specific range.

    Args:
        min_value: Minimum allowed value (inclusive)
        max_value: Maximum allowed value (inclusive)

    Returns:
        Validator function for attrs field

    Example:
        ```python
        @define
        class Command:
            limit: int = field(validator=positive_int_in_range(1, 10000))
        ```
    """

    def validator(_instance: Any, attribute: Attribute[Any], value: object) -> None:
        if not isinstance(value, int):
            raise TypeError(
                f"{attribute.name} must be an integer, got {type(value).__name__}"
            )
        if value < min_value or value > max_value:
            raise ValueError(
                f"{attribute.name} must be between {min_value} and {max_value}, got {value}"
            )

    return validator


def optional_positive_int(
    _instance: Any, attribute: Attribute[Any], value: int | None
) -> None:
    """Validates that an optional integer is positive when provided.

    Args:
        instance: The attrs instance being validated
        attribute: The field attribute being validated
        value: The integer value to validate (can be None)

    Raises:
        ValueError: If value is not None and not positive
    """
    if value is not None and value <= 0:
        raise ValueError(
            f"{attribute.name} must be positive when provided, got {value}"
        )


def optional_in_choices(choices: list[str]) -> Any:
    """Creates a validator for optional string fields that must be in a list of choices.

    Args:
        choices: List of valid string choices

    Returns:
        Validator function for attrs field

    Example:
        ```python
        @define
        class Command:
            sort_by: str | None = field(
                validator=optional_in_choices(["asc", "desc", "random"])
            )
        ```
    """

    def validator(_instance: Any, attribute: Attribute[Any], value: str | None) -> None:
        if value is not None and value not in choices:
            raise ValueError(
                f"{attribute.name} must be one of {choices}, got {value!r}"
            )

    return validator


def tracklist_or_connector_playlist(
    _instance: Any, attribute: Attribute[Any], value: Any
) -> None:
    """Validates that TrackList has tracks OR command has connector_playlist field.

    Checks the command instance for a `connector_playlist` field as an alternative
    to requiring tracks in the TrackList. This replaces the old pattern of smuggling
    ConnectorPlaylist objects through TrackList metadata.

    Args:
        _instance: The attrs command instance being validated
        attribute: The field attribute being validated
        value: The TrackList value to validate

    Raises:
        TypeError: If value is not a TrackList
        ValueError: If TrackList has no tracks and command has no connector_playlist
    """
    from src.domain.entities.track import TrackList

    if not isinstance(value, TrackList):
        raise TypeError(
            f"{attribute.name} must be a TrackList, got {type(value).__name__}"
        )

    has_tracks = bool(value.tracks)
    has_connector_playlist = bool(getattr(_instance, "connector_playlist", None))

    if not (has_tracks or has_connector_playlist):
        raise ValueError(
            f"{attribute.name} must have tracks or command must have connector_playlist"
        )


def api_batch_size_validator(
    max_batch_size_setting: str = "api.spotify_large_batch_size",
) -> Any:
    """Creates a validator for API batch size that checks against settings.

    Args:
        max_batch_size_setting: Dot-notation path to max batch size in settings

    Returns:
        Validator function for attrs field
    """

    def validator(_instance: Any, attribute: Attribute[Any], value: int) -> None:
        from src.config import settings

        # Navigate settings using dot notation
        setting_parts = max_batch_size_setting.split(".")
        max_value = settings
        for part in setting_parts:
            max_value = getattr(max_value, part)

        if not isinstance(max_value, int):
            raise TypeError(
                f"Expected int for settings.{max_batch_size_setting}, got {type(max_value).__name__}"
            )
        if value > max_value:
            raise ValueError(
                f"{attribute.name} cannot exceed {max_value} (from settings.{max_batch_size_setting}), got {value}"
            )

    return validator
