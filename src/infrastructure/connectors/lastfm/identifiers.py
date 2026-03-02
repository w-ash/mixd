"""Shared Last.fm track identifier utilities.

Last.fm identifies tracks by artist::title pairs. These functions provide
consistent creation, normalization, and parsing of these identifiers across
the track resolution and inward resolver modules.
"""


def make_lastfm_identifier(artist: str, title: str) -> str:
    """Create a normalized Last.fm track identifier from artist and title.

    Format: "artist_lower_stripped::title_lower_stripped"
    Used for deduplication and lookup across Last.fm import pipelines.
    """
    artist_normalized = artist.strip().lower() if artist else ""
    title_normalized = title.strip().lower() if title else ""
    return f"{artist_normalized}::{title_normalized}"


def parse_lastfm_identifier(identifier: str) -> tuple[str, str]:
    """Parse a Last.fm identifier back into (artist, title) components.

    Raises:
        ValueError: If identifier doesn't contain '::' separator.
    """
    if "::" not in identifier:
        raise ValueError(f"Invalid Last.fm identifier format: {identifier}")
    artist, title = identifier.split("::", 1)
    return artist, title
