"""Connector status endpoints.

Thin route handler that delegates all token checking, refresh logic,
and status determination to the connector_status application service.
"""

from fastapi import APIRouter

from src.infrastructure.connectors._shared.connector_status import (
    get_all_connector_statuses,
)
from src.interface.api.schemas.connectors import ConnectorStatusSchema

router = APIRouter(prefix="/connectors", tags=["connectors"])


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
