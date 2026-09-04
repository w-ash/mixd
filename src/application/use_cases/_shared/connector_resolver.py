"""Shared connector resolution logic for use cases.

Resolves a connector instance from the UoW's service connector provider,
gated on the capability the registry declares for it. The typed wrappers
below are the surface use cases call; each names one ``Capability`` and one
capability protocol, so a caller gets a narrowed type and a registry-backed
guarantee that the service actually offers the operation.
"""

from src.application.connector_protocols import (
    DiscogsCollectionConnector,
    LikedTrackConnector,
    LoveTrackConnector,
    PlaylistConnector,
    TidalFavoritesConnector,
    TrackConversionConnector,
    UserPlaylistsConnector,
)
from src.domain.entities.connector import Capability
from src.domain.repositories.uow import UnitOfWorkProtocol


def resolve_capability[ConnectorT](
    service: str,
    uow: UnitOfWorkProtocol,
    *,
    capability: Capability,
    protocol: type[ConnectorT],
) -> ConnectorT:
    """Resolve a connector that declares ``capability``, narrowed to ``protocol``.

    Two distinct failures, deliberately different exception types: a service
    that does not declare the capability is a caller error (``ValueError``),
    while a service that declares it but does not implement the protocol is an
    adapter bug (``TypeError``) — the registry and the class disagree.

    Args:
        service: Connector name (e.g., ``"spotify"``, ``"lastfm"``).
        uow: Unit of work providing connector access.
        capability: The registry capability the operation requires.
        protocol: Runtime-checkable capability protocol to narrow to.

    Returns:
        The connector instance, typed as ``protocol``.

    Raises:
        ValueError: If the service is unregistered or lacks the capability.
        TypeError: If the connector does not implement ``protocol``.
    """
    provider = uow.get_service_connector_provider()
    descriptor = provider.describe(service)
    if capability not in descriptor.capabilities:
        raise ValueError(f"Connector '{service}' does not support '{capability}'")
    connector = provider.get_connector(service)
    if not isinstance(connector, protocol):
        raise TypeError(
            f"Connector '{service}' declares '{capability}' but does not "
            f"implement {protocol.__name__}"
        )
    return connector


def resolve_playlist_connector(
    service: str, uow: UnitOfWorkProtocol
) -> PlaylistConnector:
    """Resolve a connector typed for playlist fetch and CRUD."""
    return resolve_capability(
        service, uow, capability="playlist_sync", protocol=PlaylistConnector
    )


def resolve_user_playlists_connector(
    service: str, uow: UnitOfWorkProtocol
) -> UserPlaylistsConnector:
    """Resolve a connector typed for listing the user's own playlists."""
    return resolve_capability(
        service, uow, capability="playlist_import", protocol=UserPlaylistsConnector
    )


def resolve_liked_track_connector(
    service: str, uow: UnitOfWorkProtocol
) -> LikedTrackConnector:
    """Resolve a connector typed for liked-track reads."""
    return resolve_capability(
        service, uow, capability="likes_import", protocol=LikedTrackConnector
    )


def resolve_love_track_connector(
    service: str, uow: UnitOfWorkProtocol
) -> LoveTrackConnector:
    """Resolve a connector typed for love-track writes."""
    return resolve_capability(
        service, uow, capability="love_tracks", protocol=LoveTrackConnector
    )


def resolve_discogs_collection_connector(
    uow: UnitOfWorkProtocol,
) -> DiscogsCollectionConnector:
    """Resolve the Discogs connector typed for raw collection reads.

    Fixed service name: the raw collection seam has no capability of its own
    (no domain entities exist for Discogs releases yet), so there is nothing
    for another connector to declare.
    """
    connector = uow.get_service_connector_provider().get_connector("discogs")
    if not isinstance(connector, DiscogsCollectionConnector):
        raise TypeError("Connector 'discogs' does not support collection reads")
    return connector


def resolve_tidal_favorites_connector(
    uow: UnitOfWorkProtocol,
) -> TidalFavoritesConnector:
    """Resolve the Tidal connector typed for raw favorites reads.

    Fixed service name for the same reason as the Discogs collection seam.
    """
    connector = uow.get_service_connector_provider().get_connector("tidal")
    if not isinstance(connector, TidalFavoritesConnector):
        raise TypeError("Connector 'tidal' does not support favorites reads")
    return connector


def resolve_track_conversion_connector(
    service: str, uow: UnitOfWorkProtocol
) -> TrackConversionConnector:
    """Resolve a connector typed for track data conversion.

    Used when converting raw track data dicts (e.g., from playlist extras)
    into ConnectorTrack domain entities. No registry capability covers this —
    it is an internal decoding detail of the adapter, not a user-facing
    operation — so the protocol check stands alone.

    Raises:
        TypeError: If the connector doesn't support track conversion.
    """
    connector = uow.get_service_connector_provider().get_connector(service)
    if not isinstance(connector, TrackConversionConnector):
        raise TypeError(f"Connector '{service}' does not support track conversion")
    return connector
