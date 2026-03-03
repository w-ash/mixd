"""Pydantic v2 schemas for connector status endpoints."""

from pydantic import BaseModel, ConfigDict


class ConnectorStatusSchema(BaseModel):
    """Current authentication status of a music service connector."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    connected: bool
    account_name: str | None = None
    token_expires_at: int | None = None
