"""Public artist pages, per connector.

The single place a connector identifier becomes a link to that service's own
artist page. It sits beside the use cases rather than in the connector registry
(``infrastructure/connectors/_shared/external_urls.py``, which reads a
``track_url`` hook off each connector config) because artists carry no registry
hook this cycle: adding one would mean editing six connector configs to state
six one-line templates. Both the API detail schema and the CLI ``artists show``
table read this function, so there is still exactly one table.

An unknown connector, or a blank identifier, yields None rather than a guessed
URL — the detail page renders the mapping without a link.
"""

from collections.abc import Callable, Mapping
from typing import Final
from urllib.parse import quote

# Last.fm addresses artists by name, not by an opaque id: its mappings store the
# name as the identifier. Every other service stores its own id.
_BUILDERS: Final[Mapping[str, Callable[[str], str]]] = {
    "spotify": lambda i: f"https://open.spotify.com/artist/{i}",
    "lastfm": lambda i: f"https://www.last.fm/music/{quote(i, safe='')}",
    "musicbrainz": lambda i: f"https://musicbrainz.org/artist/{i}",
    "discogs": lambda i: f"https://www.discogs.com/artist/{i}",
    "apple": lambda i: f"https://music.apple.com/artist/{i}",
    "apple_music": lambda i: f"https://music.apple.com/artist/{i}",
    "tidal": lambda i: f"https://tidal.com/browse/artist/{i}",
}


def connector_artist_url(connector_name: str, identifier: str) -> str | None:
    """Public page for a connector artist, or None when the service has none."""
    builder = _BUILDERS.get(connector_name)
    if builder is None or not identifier.strip():
        return None
    return builder(identifier)
