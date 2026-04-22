"""Connector status endpoints.

Thin route handler that delegates all token checking, refresh logic,
and status determination to the connector_status application service.
"""

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from src.application.runner import execute_use_case
from src.application.use_cases.import_connector_playlist_as_canonical import (
    run_import_connector_playlists_as_canonical,
)
from src.application.use_cases.list_connector_playlists import (
    ListConnectorPlaylistsCommand,
    ListConnectorPlaylistsUseCase,
)
from src.application.use_cases.sync_likes import get_all_checkpoint_statuses
from src.domain.entities.connector import Capability, derive_status_state
from src.domain.entities.playlist_link import SyncDirection
from src.infrastructure.connectors._shared.connector_status import (
    get_all_connector_statuses,
)
from src.infrastructure.connectors._shared.token_storage import get_token_storage
from src.infrastructure.connectors.discovery import discover_connectors
from src.infrastructure.connectors.protocols import ConnectorConfig
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.connectors import (
    ConnectorMetadataSchema,
    ConnectorPlaylistBrowseResponse,
    ConnectorPlaylistBrowseSchema,
    ImportConnectorPlaylistsRequest,
    ImportConnectorPlaylistsResponse,
    ImportFailureSchema,
    ImportOutcomeSchema,
)

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _require_connector(
    service: str, *, capability: Capability | None = None
) -> ConnectorConfig:
    """Resolve a service name to its registered config, raising HTTP errors on mismatch.

    Raises 404 if the connector is unknown, 501 if a required capability is
    declared as missing. Returns the resolved config so callers can read
    ``auth_method`` and ``capabilities`` without a second registry lookup.
    """
    config = discover_connectors().get(service)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {service}")
    if capability is not None and capability not in config["capabilities"]:
        raise HTTPException(
            status_code=501,
            detail=f"{service} does not support '{capability}'",
        )
    return config


@router.get("")
async def get_connectors(
    user_id: str = Depends(get_current_user_id),
) -> list[ConnectorMetadataSchema]:
    """Return rich metadata for every registered connector.

    Merges static registry data (display_name, category, capabilities,
    auth_method) with the live status probe (connected, account_name,
    token_expires_at, auth_error), freshness data from ``DBSyncCheckpoint``
    (last_synced_at), and derives the UI-facing ``status`` enum.
    """
    registry = discover_connectors()
    # Probes hit TokenStorage; checkpoint fetch hits the UoW-managed session.
    # Independent DB paths — run concurrently.
    async with asyncio.TaskGroup() as tg:
        statuses_task = tg.create_task(get_all_connector_statuses(user_id))
        last_synced_task = tg.create_task(_last_synced_by_service(user_id))
    by_name = {s.name: s for s in statuses_task.result()}
    last_synced = last_synced_task.result()

    return [
        ConnectorMetadataSchema(
            name=name,
            display_name=config["display_name"],
            category=config["category"],
            auth_method=config["auth_method"],
            status=derive_status_state(by_name[name]),
            connected=by_name[name].connected,
            account_name=by_name[name].account_name,
            token_expires_at=by_name[name].token_expires_at,
            auth_error=by_name[name].auth_error,
            last_synced_at=last_synced.get(name),
            capabilities=sorted(config["capabilities"]),
        )
        for name, config in registry.items()
    ]


async def _last_synced_by_service(user_id: str) -> dict[str, datetime]:
    """Return the most recent ``last_sync_timestamp`` per service for a user.

    Reuses the existing ``get_all_checkpoint_statuses`` use case (also powers
    the imports + CLI history status views), keeping the "how do we know
    when a user last synced" logic in one place.
    """
    statuses = await get_all_checkpoint_statuses(user_id=user_id)
    latest: dict[str, datetime] = {}
    for s in statuses:
        if s.last_sync_timestamp is None:
            continue
        existing = latest.get(s.service)
        if existing is None or s.last_sync_timestamp > existing:
            latest[s.service] = s.last_sync_timestamp
    return latest


@router.delete("/{service}/token", status_code=204)
async def delete_connector_token(
    service: str,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Remove stored OAuth token for a connector, disconnecting it.

    Only connectors declaring ``auth_method="oauth"`` in the registry can be
    disconnected; anything else (public APIs, coming-soon stubs) returns 400.
    """
    config = _require_connector(service)
    if config["auth_method"] != "oauth":
        raise HTTPException(status_code=400, detail=f"Cannot disconnect {service}")
    storage = get_token_storage()
    await storage.delete_token(service, user_id)


@router.get("/{service}/playlists")
async def list_connector_playlists(
    service: str,
    user_id: str = Depends(get_current_user_id),
    force_refresh: bool = Query(
        default=False,
        description="Bypass the DBConnectorPlaylist cache and re-fetch from the connector",
    ),
) -> ConnectorPlaylistBrowseResponse:
    """List the user's playlists on the given connector for the browser dialog.

    Cache-first: reads from ``connector_playlists`` unless ``force_refresh``
    is true. Per-playlist ``import_status`` reflects whether this user has
    already linked the connector playlist into Mixd. Connectors that don't
    declare ``playlist_import`` in their capabilities return 501.
    """
    _require_connector(service, capability="playlist_import")
    command = ListConnectorPlaylistsCommand(
        user_id=user_id, connector_name=service, force_refresh=force_refresh
    )
    result = await execute_use_case(
        lambda uow: ListConnectorPlaylistsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return ConnectorPlaylistBrowseResponse(
        data=[
            ConnectorPlaylistBrowseSchema.model_validate(p) for p in result.playlists
        ],
        from_cache=result.from_cache,
        fetched_at=result.fetched_at,
    )


@router.post("/{service}/playlists/import")
async def import_connector_playlists(
    service: str,
    body: ImportConnectorPlaylistsRequest,
    user_id: str = Depends(get_current_user_id),
) -> ImportConnectorPlaylistsResponse:
    """Import a batch of connector playlists with a chosen sync direction.

    Non-atomic: one failing playlist doesn't abort the others. Each
    successful import creates a canonical ``Playlist`` + ``PlaylistLink``;
    previously-imported playlists with a known snapshot are short-circuited
    into ``skipped_unchanged``.
    """
    _require_connector(service, capability="playlist_import")
    result = await run_import_connector_playlists_as_canonical(
        user_id=user_id,
        connector_name=service,
        connector_playlist_ids=body.connector_playlist_ids,
        sync_direction=SyncDirection(body.sync_direction),
    )
    return ImportConnectorPlaylistsResponse(
        succeeded=[
            ImportOutcomeSchema(
                connector_playlist_identifier=o.connector_playlist_identifier,
                canonical_playlist_id=str(o.canonical_playlist_id),
                resolved=o.resolved,
                unresolved=o.unresolved,
            )
            for o in result.succeeded
        ],
        skipped_unchanged=list(result.skipped_unchanged),
        failed=[
            ImportFailureSchema(
                connector_playlist_identifier=f.connector_playlist_identifier,
                message=f.message,
            )
            for f in result.failed
        ],
    )
