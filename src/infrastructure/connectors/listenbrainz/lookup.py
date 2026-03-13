"""ListenBrainz Labs API client for Spotify ID resolution.

Lightweight utility (not a full connector) that queries ListenBrainz's
metadata lookup API to resolve artist+title → Spotify track ID. This
complements the Spotify search API by providing an independent matching
source based on MusicBrainz's linked data graph.

No authentication required. Uses shared httpx client patterns with
event hooks for structured logging.
"""

# pyright: reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
# Legitimate Any: JSON API response parsing

import httpx

from src.config import get_logger

logger = get_logger(__name__)


class ListenBrainzLookup:
    """Targeted ListenBrainz Labs API for Spotify ID resolution."""

    _client: httpx.AsyncClient

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def spotify_id_from_metadata(
        self, artist_name: str, recording_name: str
    ) -> str | None:
        """Look up Spotify track ID via ListenBrainz metadata matching.

        Queries the ListenBrainz Labs spotify-id-from-metadata endpoint
        which uses MusicBrainz linked data to find the canonical Spotify
        track ID for a given artist + recording name.

        Returns:
            Spotify track ID string, or None if no match found.
        """
        try:
            response = await self._client.post(
                "/spotify-id-from-metadata/json",
                json=[
                    {
                        "artist_name": artist_name,
                        "recording_name": recording_name,
                    }
                ],
            )
            response.raise_for_status()
            data = response.json()

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            result = data[0]
            spotify_id = result.get("spotify_track_id")
            if not spotify_id or not isinstance(spotify_id, str):
                return None

            # Strip "spotify:track:" prefix if present
            spotify_id = spotify_id.removeprefix("spotify:track:")

        except httpx.HTTPStatusError as e:
            logger.debug(
                f"ListenBrainz lookup HTTP error for {artist_name} - {recording_name}: {e.response.status_code}"
            )
            return None
        except (httpx.RequestError, KeyError, IndexError, TypeError) as e:
            logger.debug(
                f"ListenBrainz lookup failed for {artist_name} - {recording_name}: {e}"
            )
            return None
        else:
            return spotify_id

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
