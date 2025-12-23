"""Attrs field validators for command classes.

Provides reusable attrs validators for command field validation using Python 3.13+
patterns. These validators execute at construction time for fail-fast behavior.

Example usage:
    ```python
    from attrs import define, field
    from src.application.use_cases._shared.command_validators import (
        non_empty_string,
        positive_int_in_range
    )

    @define(frozen=True, slots=True)
    class CreatePlaylistCommand:
        name: str = field(validator=non_empty_string)
        limit: int = field(validator=positive_int_in_range(1, 10000))
    ```
"""

from __future__ import annotations

from typing import Any

import attrs
from attrs import Attribute

from src.config.constants import BusinessLimits


def non_empty_string(instance: Any, attribute: Attribute, value: str) -> None:
    """Validates that a string field is non-empty after stripping whitespace.

    Args:
        instance: The attrs instance being validated
        attribute: The field attribute being validated
        value: The string value to validate

    Raises:
        ValueError: If the string is empty or contains only whitespace
    """
    if not value or not value.strip():
        raise ValueError(
            f"{attribute.name} must be a non-empty string, got: {value!r}"
        )


def non_empty_list(instance: Any, attribute: Attribute, value: list) -> None:
    """Validates that a list field is non-empty.

    Args:
        instance: The attrs instance being validated
        attribute: The field attribute being validated
        value: The list value to validate

    Raises:
        ValueError: If the list is empty
    """
    if not value:
        raise ValueError(
            f"{attribute.name} must be a non-empty list"
        )


def positive_int_in_range(
    min_value: int = 1,
    max_value: int = BusinessLimits.MAX_USER_LIMIT
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
    def validator(instance: Any, attribute: Attribute, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError(
                f"{attribute.name} must be an integer, got {type(value).__name__}"
            )
        if value < min_value or value > max_value:
            raise ValueError(
                f"{attribute.name} must be between {min_value} and {max_value}, "
                f"got {value}"
            )
    return validator


def optional_positive_int(instance: Any, attribute: Attribute, value: int | None) -> None:
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
    def validator(instance: Any, attribute: Attribute, value: str | None) -> None:
        if value is not None and value not in choices:
            raise ValueError(
                f"{attribute.name} must be one of {choices}, got {value!r}"
            )
    return validator


def tracklist_has_tracks_or_metadata(
    metadata_key: str = "connector_playlist"
) -> Any:
    """Creates a validator that ensures TrackList has tracks or specific metadata.

    This is useful for commands that accept either explicit tracks or metadata
    that will be used to fetch tracks later.

    Args:
        metadata_key: The metadata key to check for when tracks are empty

    Returns:
        Validator function for attrs field

    Example:
        ```python
        @define
        class CreatePlaylistCommand:
            tracklist: TrackList = field(
                validator=tracklist_has_tracks_or_metadata("connector_playlist")
            )
        ```
    """
    def validator(instance: Any, attribute: Attribute, value: Any) -> None:
        # Import here to avoid circular dependency
        from src.domain.entities.track import TrackList

        if not isinstance(value, TrackList):
            raise TypeError(
                f"{attribute.name} must be a TrackList, got {type(value).__name__}"
            )

        has_tracks = bool(value.tracks)
        has_metadata = bool(value.metadata and value.metadata.get(metadata_key))

        if not (has_tracks or has_metadata):
            raise ValueError(
                f"{attribute.name} must have tracks or '{metadata_key}' in metadata"
            )
    return validator


def api_batch_size_validator(
    max_batch_size_setting: str = "api.spotify_large_batch_size"
) -> Any:
    """Creates a validator for API batch size that checks against settings.

    Args:
        max_batch_size_setting: Dot-notation path to max batch size in settings

    Returns:
        Validator function for attrs field
    """
    def validator(instance: Any, attribute: Attribute, value: int) -> None:
        from src.config import settings

        # Navigate settings using dot notation
        setting_parts = max_batch_size_setting.split(".")
        max_value = settings
        for part in setting_parts:
            max_value = getattr(max_value, part)

        if value > max_value:
            raise ValueError(
                f"{attribute.name} cannot exceed {max_value} (from settings.{max_batch_size_setting}), "
                f"got {value}"
            )
    return validator


# Convenience validators using attrs.validators combinators
def and_(*validators: Any) -> Any:
    """Combines multiple validators with AND logic.

    All validators must pass for validation to succeed.

    Args:
        *validators: Variable number of validator functions

    Returns:
        Combined validator function

    Example:
        ```python
        @define
        class Command:
            id: str = field(validator=and_(
                attrs.validators.instance_of(str),
                non_empty_string
            ))
        ```
    """
    return attrs.validators.and_(*validators)


def optional(validator: Any) -> Any:
    """Makes a validator optional (allows None).

    Args:
        validator: The validator to make optional

    Returns:
        Optional validator that allows None

    Example:
        ```python
        @define
        class Command:
            description: str | None = field(validator=optional(non_empty_string))
        ```
    """
    return attrs.validators.optional(validator)


def instance_of(type_: type) -> Any:
    """Validates that a field is an instance of a specific type.

    Args:
        type_: The expected type

    Returns:
        Instance validator

    Example:
        ```python
        @define
        class Command:
            tracklist: TrackList = field(validator=instance_of(TrackList))
        ```
    """
    return attrs.validators.instance_of(type_)
