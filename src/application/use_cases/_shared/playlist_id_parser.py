"""Playlist identifier parser — normalizes URLs, URIs, and raw IDs to raw IDs.

Accepts any of:
- Raw ID: ``37i9dQZF1DZ06evO05tE88``
- Spotify URI: ``spotify:playlist:37i9dQZF1DZ06evO05tE88``
- Spotify URL: ``https://open.spotify.com/playlist/37i9dQZF1DZ06evO05tE88``
- URL with query params: ``https://open.spotify.com/playlist/37i...?si=abc``

Returns the raw ID regardless of input format. Connector-aware — only Spotify
has URI/URL formats currently.
"""

import re

# Spotify playlist URL: https://open.spotify.com/playlist/<id>
_SPOTIFY_URL_RE = re.compile(r"^https?://open\.spotify\.com/playlist/([A-Za-z0-9]+)")

# Spotify URI: spotify:playlist:<id>
_SPOTIFY_URI_RE = re.compile(r"^spotify:playlist:([A-Za-z0-9]+)$")


def parse_playlist_identifier(connector: str, raw_input: str) -> str:
    """Normalize a playlist identifier to a raw ID.

    Args:
        connector: Connector name (e.g., "spotify", "apple_music").
        raw_input: User-provided value — can be URL, URI, or raw ID.

    Returns:
        The raw playlist ID for the given connector.

    Raises:
        ValueError: If the input cannot be parsed.
    """
    raw_input = raw_input.strip()
    if not raw_input:
        raise ValueError("Playlist identifier cannot be empty")

    if connector == "spotify":
        return _parse_spotify_id(raw_input)

    # Other connectors: treat input as raw ID
    return raw_input


def _parse_spotify_id(raw_input: str) -> str:
    """Extract Spotify playlist ID from URL, URI, or raw ID."""
    # Try URL first
    if match := _SPOTIFY_URL_RE.match(raw_input):
        return match.group(1)

    # Try URI
    if match := _SPOTIFY_URI_RE.match(raw_input):
        return match.group(1)

    # Assume raw ID — validate it looks reasonable (alphanumeric, 22 chars typical)
    if re.fullmatch(r"[A-Za-z0-9]+", raw_input):
        return raw_input

    raise ValueError(
        f"Cannot parse Spotify playlist identifier: {raw_input!r}. "
        "Expected a playlist URL, URI (spotify:playlist:...), or raw ID."
    )
