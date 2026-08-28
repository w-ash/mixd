"""Pydantic models for ListenBrainz Labs API bodies.

Boundary rule: raw response payloads validate into these models at the
client; only typed models flow downstream.
"""

from pydantic import BaseModel, Field, RootModel


class SpotifyIdLookupQuery(BaseModel):
    """One spotify-id-from-metadata query — the endpoint requires all three fields."""

    artist_name: str
    release_name: str
    track_name: str


class SpotifyIdLookupResult(BaseModel):
    """One result row: the echoed query fields plus the matched Spotify ids.

    ``spotify_track_ids`` holds bare track ids (no ``spotify:track:`` prefix);
    a miss is echoed with an empty list, never dropped from the array.
    """

    artist_name: str
    release_name: str
    track_name: str
    spotify_track_ids: list[str] = Field(default_factory=list)


class SpotifyIdLookupResponse(RootModel[list[SpotifyIdLookupResult]]):
    """The endpoint's array body: one echoed row per query, in request order."""
