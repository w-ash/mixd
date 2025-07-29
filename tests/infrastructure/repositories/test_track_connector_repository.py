"""Unit tests for TrackConnectorRepository primary mapping functionality."""

from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
)


class TestSetPrimaryMappingSignature:
    """Test method signature and interface compliance."""

    def test_method_signature(self):
        """Test that set_primary_mapping has correct signature."""
        import inspect

        method = TrackConnectorRepository.set_primary_mapping
        signature = inspect.signature(method)

        params = list(signature.parameters.keys())
        expected = ["self", "track_id", "connector_track_id", "connector_name"]

        assert all(param in params for param in expected)
        assert signature.return_annotation == bool
        assert inspect.iscoroutinefunction(method)

    def test_method_exists(self):
        """Test that the method exists and is callable."""
        assert hasattr(TrackConnectorRepository, "set_primary_mapping")
        assert callable(TrackConnectorRepository.set_primary_mapping)
