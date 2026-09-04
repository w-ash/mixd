"""Spotify playlist identifier parsing — URL, URI, and base62 id forms.

Accepts any of:
- Raw ID: ``37i9dQZF1DZ06evO05tE88``
- Spotify URI: ``spotify:playlist:37i9dQZF1DZ06evO05tE88``
- Spotify URL: ``https://open.spotify.com/playlist/37i9dQZF1DZ06evO05tE88``
- URL with query params: ``https://open.spotify.com/playlist/37i...?si=abc``

Returns the raw ID regardless of input format.
"""

import re

# Spotify playlist URL: https://open.spotify.com/playlist/<id>
_SPOTIFY_URL_RE = re.compile(r"^https?://open\.spotify\.com/playlist/([A-Za-z0-9]+)")

# Spotify URI: spotify:playlist:<id>
_SPOTIFY_URI_RE = re.compile(r"^spotify:playlist:([A-Za-z0-9]+)$")


def parse_spotify_playlist_id(raw_input: str) -> str:
    """Extract a Spotify playlist ID from a URL, URI, or raw ID.

    Args:
        raw_input: User-provided value

    Returns:
        The raw playlist ID

    Raises:
        ValueError: If the input is empty or cannot be parsed
    """
    stripped = raw_input.strip()
    if not stripped:
        raise ValueError("Playlist identifier cannot be empty")

    # Try URL first
    if match := _SPOTIFY_URL_RE.match(stripped):
        return match.group(1)

    # Try URI
    if match := _SPOTIFY_URI_RE.match(stripped):
        return match.group(1)

    # Assume raw ID — validate it looks reasonable (alphanumeric, 22 chars typical)
    if re.fullmatch(r"[A-Za-z0-9]+", stripped):
        return stripped

    raise ValueError(
        f"Cannot parse Spotify playlist identifier: {stripped!r}. "
        "Expected a playlist URL, URI (spotify:playlist:...), or raw ID."
    )
