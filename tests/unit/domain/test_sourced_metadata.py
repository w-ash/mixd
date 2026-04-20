"""Tests for shared source priority logic.

Validates SOURCE_PRIORITY ordering and the should_override() conflict
resolution function used by preferences, tags, and playlist metadata.
"""

from src.domain.entities.sourced_metadata import (
    SOURCE_PRIORITY,
    should_override,
)


class TestSourcePriority:
    """SOURCE_PRIORITY ordering: manual > playlist_assignment > service_import."""

    def test_manual_is_highest(self) -> None:
        assert SOURCE_PRIORITY["manual"] > SOURCE_PRIORITY["playlist_assignment"]
        assert SOURCE_PRIORITY["manual"] > SOURCE_PRIORITY["service_import"]

    def test_playlist_assignment_is_middle(self) -> None:
        assert (
            SOURCE_PRIORITY["playlist_assignment"] > SOURCE_PRIORITY["service_import"]
        )
        assert SOURCE_PRIORITY["playlist_assignment"] < SOURCE_PRIORITY["manual"]


class TestShouldOverride:
    """should_override returns True only when new source is strictly higher."""

    def test_manual_overrides_service_import(self) -> None:
        assert should_override("service_import", "manual") is True

    def test_service_import_does_not_override_manual(self) -> None:
        assert should_override("manual", "service_import") is False

    def test_same_priority_does_not_override(self) -> None:
        assert should_override("service_import", "service_import") is False
        assert should_override("manual", "manual") is False
        assert should_override("playlist_assignment", "playlist_assignment") is False

    def test_playlist_assignment_overrides_service_import(self) -> None:
        assert should_override("service_import", "playlist_assignment") is True

    def test_manual_overrides_playlist_assignment(self) -> None:
        assert should_override("playlist_assignment", "manual") is True

    def test_service_import_does_not_override_playlist_assignment(self) -> None:
        assert should_override("playlist_assignment", "service_import") is False
