"""Characterization tests for template_utils.

Locks down render_playlist_config_templates behavior before cleanup.
"""

import datetime as dt

import pytest

from src.application.workflows.nodes import template_utils
from src.application.workflows.nodes.template_utils import (
    render_playlist_config_templates,
)

# 04:05 UTC is 23:05 the previous day in UTC-5, so a local-time render
# shows a different date and time.
_NOW_UTC = dt.datetime(2025, 7, 9, 4, 5, tzinfo=dt.UTC)


class _FrozenClock(dt.datetime):
    """Clock boundary stand-in: ``now(tz)`` returns a fixed instant.

    With no ``tz`` it answers in UTC-5, standing in for a non-UTC local zone.
    """

    @classmethod
    def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:
        return _NOW_UTC.astimezone(tz or dt.timezone(dt.timedelta(hours=-5)))


class TestRenderPlaylistConfigTemplates:
    """Tests for render_playlist_config_templates."""

    def test_track_count_replacement(self):
        """'{track_count}' is replaced with actual count."""
        config = {"name": "{track_count} tracks"}
        result = render_playlist_config_templates(config, 42)
        assert result["name"] == "42 tracks"

    @pytest.mark.parametrize(
        ("template", "expected"),
        [
            ("Playlist {date}", "Playlist 2025-07-09"),
            ("Playlist at {time}", "Playlist at 04:05"),
            ("Generated {datetime}", "Generated 2025-07-09 04:05"),
        ],
    )
    def test_clock_placeholders_render_from_utc(
        self, monkeypatch: pytest.MonkeyPatch, template: str, expected: str
    ):
        """{date}, {time} and {datetime} render the current UTC clock, zero-padded."""
        monkeypatch.setattr(template_utils, "datetime", _FrozenClock)

        result = render_playlist_config_templates({"name": template}, 10)

        assert result["name"] == expected

    def test_non_template_passthrough(self):
        """Strings without templates pass through unchanged."""
        config = {"name": "Static Name", "description": "No templates here"}
        result = render_playlist_config_templates(config, 5)
        assert result["name"] == "Static Name"
        assert result["description"] == "No templates here"

    def test_description_also_rendered(self):
        """Templates in description field are rendered too."""
        config = {"description": "{track_count} curated tracks as of {date}"}
        result = render_playlist_config_templates(config, 20)
        assert "20 curated tracks as of" in result["description"]

    def test_non_string_fields_untouched(self):
        """Non-string config fields pass through unchanged."""
        config = {"name": "Test", "count": 42, "flag": True}
        result = render_playlist_config_templates(config, 10)
        assert result["count"] == 42
        assert result["flag"] is True

    def test_empty_config(self):
        """Empty config returns empty dict copy."""
        result = render_playlist_config_templates({}, 0)
        assert result == {}

    def test_does_not_mutate_input(self):
        """Input config dict is not mutated."""
        config = {"name": "{track_count} songs"}
        original = config.copy()
        render_playlist_config_templates(config, 5)
        assert config == original
