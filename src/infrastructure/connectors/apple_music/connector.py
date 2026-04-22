"""Apple Music connector stub.

Registered in the connector discovery registry so the Integrations page
can surface Apple Music with a "coming soon" state. The factory returns a
bare ``object()`` because no Apple Music API integration exists yet —
consumers must gate on the ``coming_soon`` auth method before attempting
to resolve it as a working connector.
"""

from src.infrastructure.connectors.protocols import ConnectorConfig


def get_connector_config() -> ConnectorConfig:
    """Apple Music connector configuration (stub, pre-implementation)."""
    from src.infrastructure.connectors._shared.connector_status import (
        get_apple_music_status,
    )

    return {
        "dependencies": [],
        "factory": lambda _params: object(),
        "metrics": {},
        "display_name": "Apple Music",
        "category": "streaming",
        "auth_method": "coming_soon",
        "capabilities": frozenset(),
        "status_fn": get_apple_music_status,
        "build_auth_url": None,
    }
