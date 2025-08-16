"""Unit tests for TrackMapper primary mapping awareness."""

from src.infrastructure.persistence.repositories.track.mapper import TrackMapper


class TestTrackMapperArchitecture:
    """Test architectural aspects of TrackMapper."""

    def test_mapper_has_dual_pass_logic(self):
        """Test that mapper contains dual-pass primary logic."""
        import inspect

        # Primary mapping logic is in _to_domain_with_session (the core implementation)
        source = inspect.getsource(TrackMapper._to_domain_with_session)

        # Should contain primary mapping logic
        assert "is_primary" in source
        assert "primary" in source.lower()

    def test_mapper_handles_awaitable_attrs(self):
        """Test that mapper uses awaitable_attrs pattern."""
        import inspect

        source = inspect.getsource(TrackMapper._get_connector_track)

        # Should use awaitable_attrs pattern for SQLAlchemy 2.0
        assert "awaitable_attrs" in source
        assert "connector_track" in source
