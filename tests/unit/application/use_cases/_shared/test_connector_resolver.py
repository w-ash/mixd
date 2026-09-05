"""Unit tests for the capability-gated connector resolvers.

``resolve_capability`` answers two different questions with two different
exception types: a service that never declared the capability is a caller
error (``ValueError``), while a service that declared it but does not
implement the protocol is an adapter bug (``TypeError``). Both matter — the
first is a 400/501-shaped condition, the second is a wiring mistake that must
not be silently narrowed away.
"""

from unittest.mock import AsyncMock

import pytest

from src.application.connector_protocols import (
    LikedTrackConnector,
    PlaylistConnector,
)
from src.application.use_cases._shared.connector_resolver import (
    resolve_capability,
    resolve_liked_track_connector,
    resolve_love_track_connector,
    resolve_playlist_connector,
)
from src.domain.entities.connector import Capability, ConnectorDescriptor
from tests.fixtures import make_mock_connector_provider, make_mock_uow


def _uow(connector: object, *, capabilities: set[Capability], name: str = "spotify"):
    provider = make_mock_connector_provider(connector, name=name)
    provider.describe.return_value = ConnectorDescriptor(
        name=name,
        display_name=name.title(),
        category="streaming",
        auth_method="oauth",
        capabilities=frozenset(capabilities),
    )
    return make_mock_uow(connector_provider=provider)


class TestCapabilityGate:
    def test_default_mock_provider_carries_the_real_declaration(self) -> None:
        """The fixture defaults to the registry's declared set, so a gate a real
        connector would reject fails under test too: Discogs declares nothing."""
        uow = make_mock_uow(
            connector_provider=make_mock_connector_provider(AsyncMock(), name="discogs")
        )

        with pytest.raises(ValueError, match="does not declare 'playlist_sync'"):
            resolve_playlist_connector("discogs", uow)

    def test_declared_capability_returns_the_narrowed_connector(self) -> None:
        connector = AsyncMock(spec=LikedTrackConnector)
        uow = _uow(connector, capabilities={"likes_import"})

        assert resolve_liked_track_connector("spotify", uow) is connector

    def test_undeclared_capability_raises_value_error(self) -> None:
        connector = AsyncMock(spec=LikedTrackConnector)
        uow = _uow(connector, capabilities={"playlist_sync"})

        with pytest.raises(ValueError, match="does not declare 'likes_import'"):
            resolve_liked_track_connector("spotify", uow)

    def test_unknown_service_propagates_the_provider_error(self) -> None:
        uow = _uow(AsyncMock(), capabilities={"likes_import"})
        uow.get_service_connector_provider().describe.side_effect = ValueError(
            "Unknown connector: nope"
        )

        with pytest.raises(ValueError, match="Unknown connector"):
            resolve_liked_track_connector("nope", uow)

    def test_declared_but_unimplemented_raises_type_error(self) -> None:
        """The registry and the adapter disagree — a wiring bug, not a 501."""
        connector = AsyncMock(spec=LikedTrackConnector)
        uow = _uow(connector, capabilities={"love_tracks"}, name="lastfm")

        with pytest.raises(TypeError, match="LoveTrackConnector"):
            resolve_love_track_connector("lastfm", uow)

    def test_capability_and_protocol_travel_together(self) -> None:
        connector = AsyncMock(spec=PlaylistConnector)
        uow = _uow(connector, capabilities={"playlist_sync"})

        assert resolve_playlist_connector("spotify", uow) is connector
        assert (
            resolve_capability(
                "spotify", uow, capability="playlist_sync", protocol=PlaylistConnector
            )
            is connector
        )
