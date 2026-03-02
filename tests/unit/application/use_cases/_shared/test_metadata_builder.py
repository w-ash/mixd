"""Tests for PlaylistMetadataBuilder and convenience functions.

Tests the fluent builder pattern for constructing playlist operation metadata,
ensuring method chaining, timestamp formatting, and all convenience functions
produce correct output.
"""

from datetime import UTC, datetime

from src.application.use_cases._shared.metadata_builder import (
    PlaylistMetadataBuilder,
    build_api_execution_metadata,
)


class TestPlaylistMetadataBuilderChaining:
    """Test fluent builder interface returns self for method chaining."""

    def test_fluent_chaining_returns_self(self):
        """Every with_* method should return the same builder instance."""
        builder = PlaylistMetadataBuilder()

        result = (
            builder
            .with_timestamp()
            .with_operations(5, 3)
            .with_snapshot("snap_123")
            .with_track_counts(added=2, removed=1, moved=0)
            .with_validation(True)
            .with_custom("foo", "bar")
        )

        assert result is builder

    def test_each_method_individually_returns_self(self):
        """Each with_* method returns the builder for chaining."""
        builder = PlaylistMetadataBuilder()

        assert builder.with_timestamp() is builder
        assert builder.with_operations(1, 1) is builder
        assert builder.with_snapshot("s") is builder
        assert builder.with_track_counts() is builder
        assert builder.with_validation(True) is builder
        assert builder.with_custom("k", "v") is builder


class TestPlaylistMetadataBuilderFields:
    """Test that builder methods set the correct metadata fields."""

    def test_with_timestamp_sets_isoformat_timestamps(self):
        """with_timestamp should set last_modified and database_update_timestamp as ISO strings."""
        fixed_time = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        result = PlaylistMetadataBuilder().with_timestamp(fixed_time).build_dict()

        assert result["last_modified"] == fixed_time.isoformat()
        # database_update_timestamp is always datetime.now(UTC), so just check it parses
        assert "database_update_timestamp" in result
        datetime.fromisoformat(result["database_update_timestamp"])

    def test_with_timestamp_defaults_to_now(self):
        """with_timestamp without argument should use current UTC time."""
        before = datetime.now(UTC)
        result = PlaylistMetadataBuilder().with_timestamp().build_dict()
        after = datetime.now(UTC)

        last_modified = datetime.fromisoformat(result["last_modified"])
        assert before <= last_modified <= after

    def test_with_operations_sets_requested_and_applied(self):
        """with_operations should set operations_requested and operations_applied."""
        result = PlaylistMetadataBuilder().with_operations(10, 7).build_dict()

        assert result["operations_requested"] == 10
        assert result["operations_applied"] == 7

    def test_with_track_counts_sets_added_removed_moved(self):
        """with_track_counts should set tracks_added, tracks_removed, tracks_moved."""
        result = (
            PlaylistMetadataBuilder()
            .with_track_counts(added=5, removed=2, moved=1)
            .build_dict()
        )

        assert result["tracks_added"] == 5
        assert result["tracks_removed"] == 2
        assert result["tracks_moved"] == 1

    def test_with_track_counts_defaults_to_zero(self):
        """with_track_counts without arguments should default all counts to 0."""
        result = PlaylistMetadataBuilder().with_track_counts().build_dict()

        assert result["tracks_added"] == 0
        assert result["tracks_removed"] == 0
        assert result["tracks_moved"] == 0

    def test_with_snapshot_sets_snapshot_id(self):
        """with_snapshot should set snapshot_id field."""
        result = PlaylistMetadataBuilder().with_snapshot("abc123").build_dict()
        assert result["snapshot_id"] == "abc123"

    def test_with_snapshot_allows_none(self):
        """with_snapshot should accept None."""
        result = PlaylistMetadataBuilder().with_snapshot(None).build_dict()
        assert result["snapshot_id"] is None

    def test_with_validation_sets_passed_flag(self):
        """with_validation should set validation_passed field."""
        assert (
            PlaylistMetadataBuilder()
            .with_validation(True)
            .build_dict()["validation_passed"]
            is True
        )
        assert (
            PlaylistMetadataBuilder()
            .with_validation(False)
            .build_dict()["validation_passed"]
            is False
        )

    def test_with_custom_sets_arbitrary_key(self):
        """with_custom should set any key-value pair."""
        result = PlaylistMetadataBuilder().with_custom("my_key", [1, 2, 3]).build_dict()
        assert result["my_key"] == [1, 2, 3]


class TestPlaylistMetadataBuilderBuild:
    """Test build() and build_dict() output behavior."""

    def test_build_returns_accumulated_metadata(self):
        """build() should return all accumulated metadata."""
        result = (
            PlaylistMetadataBuilder()
            .with_operations(3, 3)
            .with_validation(True)
            .build()
        )

        assert result["operations_requested"] == 3
        assert result["operations_applied"] == 3
        assert result["validation_passed"] is True

    def test_build_dict_returns_copy(self):
        """build_dict() should return a copy, not the internal dict."""
        builder = PlaylistMetadataBuilder().with_custom("key", "value")
        dict1 = builder.build_dict()
        dict2 = builder.build_dict()

        assert dict1 == dict2
        assert dict1 is not dict2

    def test_empty_builder_returns_empty_dict(self):
        """Building without setting any fields should return empty dict."""
        result = PlaylistMetadataBuilder().build_dict()
        assert result == {}


class TestConvenienceFunctions:
    """Test convenience functions that wrap the builder."""

    def test_build_api_execution_metadata_success(self):
        """build_api_execution_metadata should produce correct metadata for successful execution."""
        result = build_api_execution_metadata(
            operations_count=5,
            snapshot_id="snap_abc",
            tracks_added=3,
            tracks_removed=1,
            tracks_moved=0,
            validation_passed=True,
        )

        assert result["operations_requested"] == 5
        assert result["operations_applied"] == 5
        assert result["snapshot_id"] == "snap_abc"
        assert result["tracks_added"] == 3
        assert result["tracks_removed"] == 1
        assert result["tracks_moved"] == 0
        assert result["validation_passed"] is True
        assert "last_modified" in result

    def test_build_api_execution_metadata_validation_failed(self):
        """When validation_passed=False, operations_applied should be 0."""
        result = build_api_execution_metadata(
            operations_count=5,
            snapshot_id=None,
            tracks_added=0,
            tracks_removed=0,
            tracks_moved=0,
            validation_passed=False,
        )

        assert result["operations_requested"] == 5
        assert result["operations_applied"] == 0
        assert result["validation_passed"] is False
