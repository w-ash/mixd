"""Tests for shared source priority logic.

Validates the should_override() conflict resolution used by preferences, tags,
and playlist metadata: manual > playlist_assignment > service_import.
"""

import pytest

from src.domain.entities.sourced_metadata import MetadataSource, should_override


class TestShouldOverride:
    """should_override returns True only when new source is strictly higher."""

    @pytest.mark.parametrize(
        ("existing", "new", "expected"),
        [
            ("service_import", "manual", True),
            ("service_import", "playlist_assignment", True),
            ("playlist_assignment", "manual", True),
            ("manual", "service_import", False),
            ("manual", "playlist_assignment", False),
            ("playlist_assignment", "service_import", False),
            ("service_import", "service_import", False),
            ("playlist_assignment", "playlist_assignment", False),
            ("manual", "manual", False),
        ],
    )
    def test_only_a_strictly_higher_source_overrides(
        self, existing: MetadataSource, new: MetadataSource, expected: bool
    ) -> None:
        assert should_override(existing, new) is expected
