"""Pydantic v2 schemas for connector status endpoints."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.domain.entities.playlist_assignment import AssignmentActionType


class ConnectorStatusSchema(BaseModel):
    """Current authentication status of a music service connector."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    connected: bool
    account_name: str | None = None
    token_expires_at: int | None = None


class ActiveAssignmentSchema(BaseModel):
    """One active assignment on a Spotify playlist row."""

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    action_type: AssignmentActionType
    action_value: str


class SpotifyPlaylistBrowseSchema(BaseModel):
    """One playlist row in the Spotify browser dialog.

    Mirrors ``SpotifyPlaylistView`` from the use case layer — the UI reads
    this shape directly; the API wraps the list in ``SpotifyPlaylistBrowseResponse``.
    """

    model_config = ConfigDict(from_attributes=True)

    connector_playlist_identifier: str
    connector_playlist_db_id: UUID
    name: str
    description: str | None
    owner: str | None
    image_url: str | None
    track_count: int
    snapshot_id: str | None
    collaborative: bool
    is_public: bool
    import_status: Literal["not_imported", "imported"]
    current_assignments: list[ActiveAssignmentSchema]


class SpotifyPlaylistBrowseResponse(BaseModel):
    """Full payload for GET /connectors/spotify/playlists.

    ``from_cache=True`` means the response was served from DBConnectorPlaylist
    without hitting Spotify; the refresh button in the UI sets
    ``force_refresh=true`` to force the cache miss.
    """

    data: list[SpotifyPlaylistBrowseSchema]
    from_cache: bool
    fetched_at: datetime


class ImportSpotifyPlaylistsRequest(BaseModel):
    """Body for POST /connectors/spotify/playlists/import.

    Each ID is a Spotify-native playlist ID (not a Mixd UUID). The whole
    batch shares one ``sync_direction`` — per-playlist direction overrides
    can be added later if a workflow demands it.
    """

    connector_playlist_ids: list[str]
    sync_direction: Literal["pull", "push"]


class ImportOutcomeSchema(BaseModel):
    """One successfully-imported playlist in the import response."""

    model_config = ConfigDict(from_attributes=True)

    connector_playlist_identifier: str
    canonical_playlist_id: str
    resolved: int
    unresolved: int


class ImportFailureSchema(BaseModel):
    """One playlist that errored during import."""

    model_config = ConfigDict(from_attributes=True)

    connector_playlist_identifier: str
    message: str


class ImportSpotifyPlaylistsResponse(BaseModel):
    """Grouped per-playlist outcomes so the UI can render a clear summary."""

    succeeded: list[ImportOutcomeSchema]
    skipped_unchanged: list[str]
    failed: list[ImportFailureSchema]
