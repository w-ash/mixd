"""Tests for the UoW's caching connector provider.

Covers the descriptor and service-listing surface added alongside
``get_connector``: descriptor fields come straight off the registry entry,
and an unknown service raises the same ValueError ``get_connector`` does.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork

_FAKE_REGISTRY = {
    "fakefm": {
        "factory": object,
        "metrics": {},
        "display_name": "Fake FM",
        "category": "history",
        "auth_method": "token",
        "capabilities": frozenset({"track_enrichment"}),
        "status_fn": None,
        "build_auth_url": None,
    }
}


def _provider():
    return DatabaseUnitOfWork(MagicMock()).get_service_connector_provider()


class TestDescribe:
    """describe() projects a registry entry onto a ConnectorDescriptor."""

    def test_descriptor_carries_registry_facts(self):
        with patch(
            "src.infrastructure.connectors.discover_connectors",
            return_value=_FAKE_REGISTRY,
        ):
            descriptor = _provider().describe("fakefm")

        assert descriptor.name == "fakefm"
        assert descriptor.display_name == "Fake FM"
        assert descriptor.category == "history"
        assert descriptor.auth_method == "token"
        assert descriptor.capabilities == frozenset({"track_enrichment"})

    def test_real_registry_describes_spotify(self):
        descriptor = _provider().describe("spotify")

        assert descriptor.display_name == "Spotify"
        assert "playlist_sync" in descriptor.capabilities

    def test_unknown_service_raises(self):
        with pytest.raises(ValueError, match="Unknown connector"):
            _provider().describe("no_such_service")
