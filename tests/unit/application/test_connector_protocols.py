"""Unit tests for the shared capability gate in ``connector_protocols``.

``resolve_connector_capability`` is the single implementation behind both the
use-case resolvers (``connector_resolver.resolve_capability``) and the
workflow node side (``NodeContext.get_connector``).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.connector_protocols import (
    LikedTrackConnector,
    LoveTrackConnector,
    resolve_connector_capability,
)
from src.domain.entities.connector import Capability, ConnectorDescriptor


def _provider(connector: object, *, capabilities: set[Capability]) -> MagicMock:
    provider = MagicMock()
    provider.get_connector.return_value = connector
    provider.describe.return_value = ConnectorDescriptor(
        name="spotify",
        display_name="Spotify",
        category="streaming",
        auth_method="oauth",
        capabilities=frozenset(capabilities),
    )
    return provider


class TestResolveConnectorCapability:
    def test_declared_and_implemented_returns_connector(self) -> None:
        connector = AsyncMock(spec=LikedTrackConnector)
        provider = _provider(connector, capabilities={"likes_import"})

        result = resolve_connector_capability(
            provider, "spotify", capability="likes_import", protocol=LikedTrackConnector
        )

        assert result is connector
        provider.describe.assert_called_once_with("spotify")
        provider.get_connector.assert_called_once_with("spotify")

    def test_undeclared_capability_raises_value_error_before_instantiation(
        self,
    ) -> None:
        provider = _provider(
            AsyncMock(spec=LikedTrackConnector), capabilities={"playlist_sync"}
        )

        with pytest.raises(
            ValueError, match="Connector 'spotify' does not declare 'likes_import'"
        ):
            resolve_connector_capability(
                provider,
                "spotify",
                capability="likes_import",
                protocol=LikedTrackConnector,
            )
        provider.get_connector.assert_not_called()

    def test_declared_but_unimplemented_raises_type_error(self) -> None:
        provider = _provider(
            AsyncMock(spec=LikedTrackConnector), capabilities={"love_tracks"}
        )

        with pytest.raises(
            TypeError,
            match="declares 'love_tracks' but does not implement LoveTrackConnector",
        ):
            resolve_connector_capability(
                provider,
                "spotify",
                capability="love_tracks",
                protocol=LoveTrackConnector,
            )

    def test_unknown_connector_propagates_provider_error(self) -> None:
        provider = MagicMock()
        provider.describe.side_effect = ValueError("Unknown connector: nope")

        with pytest.raises(ValueError, match="Unknown connector: nope"):
            resolve_connector_capability(
                provider,
                "nope",
                capability="likes_import",
                protocol=LikedTrackConnector,
            )
        provider.get_connector.assert_not_called()
