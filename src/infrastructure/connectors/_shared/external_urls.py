"""Registry-declared links out to a connector's own web pages.

Each connector declares ``track_url`` and ``playlist_url`` in its config; this
module is the single reader, so no surface grows a URL switch of its own.

Lookups arrive keyed either by the registry name (``apple_music``) or by the
service name stored on a data row (``apple``), so a connector is matched on its
registry key first and then on ``play_service_name`` — the alias the play and
mapping tables key on. An unknown service, or a connector with no hook for that
entity, yields None rather than a guessed URL.
"""

from src.infrastructure.connectors.protocols import ConnectorConfig


def _config_for(service: str) -> ConnectorConfig | None:
    """Registry entry named ``service`` or carrying it as its data-plane alias."""
    from src.infrastructure.connectors.discovery import discover_connectors

    configs = discover_connectors()
    if (config := configs.get(service)) is not None:
        return config
    return next(
        (
            config
            for name, config in configs.items()
            if config.get("play_service_name", name) == service
        ),
        None,
    )


def connector_track_url(service: str, connector_track_id: str) -> str | None:
    """Public page for a connector track, or None when the connector has none."""
    config = _config_for(service)
    hook = config.get("track_url") if config else None
    return hook(connector_track_id) if hook else None


def connector_playlist_url(service: str, connector_playlist_id: str) -> str | None:
    """Public page for a connector playlist, or None when the connector has none."""
    config = _config_for(service)
    hook = config.get("playlist_url") if config else None
    return hook(connector_playlist_id) if hook else None
