"""Pydantic v2 schemas for connector status endpoints."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.domain.entities.connector import (
    Capability,
    ConnectorAuthError,
    ConnectorAuthMethod,
    ConnectorCategory,
    ConnectorStatusState,
)
from src.domain.entities.playlist_assignment import AssignmentActionType


class ConnectorMetadataSchema(BaseModel):
    """Descriptor for a registered music service connector.

    Carries identity, category, authentication method, runtime status, and
    the capability set — everything the frontend needs to render a connector
    generically. The ``status`` field is backend-computed from ``connected``
    + ``token_expires_at`` + ``auth_method`` so the frontend renders a single
    enum instead of recomputing the state client-side.
    """

    model_config = ConfigDict(from_attributes=True)

    name: str
    display_name: str
    category: ConnectorCategory
    auth_method: ConnectorAuthMethod
    status: ConnectorStatusState
    connected: bool
    account_name: str | None = None
    token_expires_at: int | None = None
    capabilities: list[Capability]
    # Server-observed auth failure code. Populated only when the probe
    # detected an unusable credential; ``None`` otherwise.
    auth_error: ConnectorAuthError | None = None
    # Most recent successful per-service sync across all entity types, sourced
    # from ``DBSyncCheckpoint``. Lets the UI render contextual status like
    # "Synced 2h ago" instead of a bare connected indicator.
    last_synced_at: datetime | None = None


class ActiveAssignmentSchema(BaseModel):
    """One active assignment on a connector playlist row."""

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    action_type: AssignmentActionType
    action_value: str


class ConnectorPlaylistBrowseSchema(BaseModel):
    """One playlist row in the connector browser dialog.

    Mirrors ``ConnectorPlaylistView`` from the use case layer — the UI reads
    this shape directly; the API wraps the list in ``ConnectorPlaylistBrowseResponse``.
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


class ConnectorPlaylistBrowseResponse(BaseModel):
    """Full payload for GET /connectors/{service}/playlists.

    ``from_cache=True`` means the response was served from DBConnectorPlaylist
    without hitting the upstream connector; the refresh button in the UI
    sets ``force_refresh=true`` to force the cache miss.
    """

    data: list[ConnectorPlaylistBrowseSchema]
    from_cache: bool
    fetched_at: datetime


class ImportConnectorPlaylistsRequest(BaseModel):
    """Body for POST /connectors/{service}/playlists/import.

    Each ID is a provider-native playlist ID (not a Mixd UUID). The whole
    batch shares one ``sync_direction`` — per-playlist direction overrides
    can be added later if a workflow demands it.

    The endpoint returns ``OperationStartedResponse``; per-playlist outcomes
    (succeeded / skipped_unchanged / failed with track counts and failure
    messages) stream as SSE sub-operation events on
    ``GET /api/v1/operations/{operation_id}/progress``.
    """

    connector_playlist_ids: list[str]
    sync_direction: Literal["pull", "push"]
