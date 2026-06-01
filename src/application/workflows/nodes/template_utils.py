"""Template rendering utilities for playlist naming.

Provides simple template string processing for playlist names and descriptions
with minimal overhead and clean separation of concerns.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from src.domain.entities.shared import JsonValue


def render_playlist_config_templates(
    config: Mapping[str, JsonValue], track_count: int
) -> dict[str, JsonValue]:
    """Render template strings in playlist config with dynamic values.

    Processes 'name' and 'description' fields in config dict, replacing
    template parameters with actual values. Returns new config dict with
    rendered strings.

    Supported template parameters:
    - {track_count}: Number of tracks in the playlist
    - {date}: Current date in YYYY-MM-DD format
    - {time}: Current time in HH:MM format
    - {datetime}: Combined date and time in YYYY-MM-DD HH:MM format

    Args:
        config: Configuration dict potentially containing template strings
        track_count: Number of tracks for {track_count} parameter

    Returns:
        New config dict with template strings rendered to actual values

    Example:
        >>> config = {
        ...     "name": "Obsessions {date}",
        ...     "description": "{track_count} tracks",
        ... }
        >>> render_playlist_config_templates(config, 42)
        {"name": "Obsessions 2025-07-29", "description": "42 tracks"}
    """
    if not config:
        return dict(config)

    # Build template context from current datetime and track count
    now = datetime.now(UTC)
    context = {
        "track_count": track_count,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "datetime": now.strftime("%Y-%m-%d %H:%M"),
    }

    # Create new config dict to avoid mutating input
    result = dict(config)

    # Render templates in name and description fields
    for field in ["name", "description"]:
        raw = result.get(field)
        if raw and isinstance(raw, str):
            template: str = raw
            # Skip processing if no template parameters present
            if "{" not in template:
                continue

            # Simple string replacement for each template parameter
            for key, value in context.items():
                template = template.replace(f"{{{key}}}", str(value))

            result[field] = template

    return result
