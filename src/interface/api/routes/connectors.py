"""Connector status endpoints.

Thin route handler that delegates all token checking, refresh logic,
and status determination to the connector_status application service.
"""

from fastapi import APIRouter, HTTPException

from src.infrastructure.connectors._shared.connector_status import (
    get_all_connector_statuses,
)
from src.infrastructure.connectors._shared.token_storage import get_token_storage
from src.interface.api.schemas.connectors import ConnectorStatusSchema

router = APIRouter(prefix="/connectors", tags=["connectors"])

_CONNECTABLE_SERVICES = {"spotify", "lastfm"}


@router.get("")
async def get_connectors() -> list[ConnectorStatusSchema]:
    """Get authentication status of all configured connectors."""
    statuses = await get_all_connector_statuses()
    return [
        ConnectorStatusSchema(
            name=s.name,
            connected=s.connected,
            account_name=s.account_name,
            token_expires_at=s.token_expires_at,
        )
        for s in statuses
    ]


@router.delete("/{service}/token", status_code=204)
async def delete_connector_token(service: str) -> None:
    """Remove stored OAuth token for a connector, disconnecting it."""
    if service not in _CONNECTABLE_SERVICES:
        raise HTTPException(status_code=400, detail=f"Cannot disconnect {service}")
    storage = get_token_storage()
    await storage.delete_token(service)
