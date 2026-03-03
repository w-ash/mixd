"""Shared connector resolution logic for use cases.

Resolves a connector instance from the UoW's service connector provider
with consistent validation. Typed resolver variants narrow ``Any`` to
capability protocols at call sites.
"""

from typing import cast

from src.application.connector_protocols import (
    LikedTrackConnector,
    LoveTrackConnector,
    PlaylistConnector,
    TrackConversionConnector,
)
from src.domain.repositories import UnitOfWorkProtocol


def resolve_connector(
    service: str,
    uow: UnitOfWorkProtocol,
    *,
    validate: bool = True,
) -> object:
    """Resolve a connector by service name from the UoW provider.

    Args:
        service: Connector name (e.g., "spotify", "lastfm").
        uow: Unit of work providing connector access.
        validate: If True, raise ValueError when connector is not available.

    Returns:
        The connector instance.

    Raises:
        ValueError: If connector is not available and validate is True.
    """
    provider = uow.get_service_connector_provider()
    connector = provider.get_connector(service)
    if validate and not connector:
        raise ValueError(f"Connector '{service}' not available")
    return connector


def resolve_liked_track_connector(uow: UnitOfWorkProtocol) -> LikedTrackConnector:
    """Resolve Spotify connector typed for liked-track reads."""
    return cast(LikedTrackConnector, resolve_connector("spotify", uow))


def resolve_love_track_connector(uow: UnitOfWorkProtocol) -> LoveTrackConnector:
    """Resolve Last.fm connector typed for love-track writes."""
    return cast(LoveTrackConnector, resolve_connector("lastfm", uow))


def resolve_playlist_connector(
    service: str, uow: UnitOfWorkProtocol
) -> PlaylistConnector:
    """Resolve any connector typed for playlist CRUD operations.

    Raises TypeError if the connector doesn't support playlist operations.
    """
    connector = resolve_connector(service, uow)
    if not hasattr(connector, "get_playlist_details"):
        raise TypeError(f"Connector '{service}' does not support playlist operations")
    return cast(PlaylistConnector, connector)


def resolve_track_conversion_connector(
    service: str, uow: UnitOfWorkProtocol
) -> TrackConversionConnector:
    """Resolve a connector typed for track data conversion.

    Used when converting raw track data dicts (e.g., from playlist extras)
    into ConnectorTrack domain entities.

    Raises TypeError if the connector doesn't support track conversion.
    """
    connector = resolve_connector(service, uow)
    if not hasattr(connector, "convert_track_to_connector"):
        raise TypeError(f"Connector '{service}' does not support track conversion")
    return cast(TrackConversionConnector, connector)
