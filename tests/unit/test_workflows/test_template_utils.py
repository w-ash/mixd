"""Characterization tests for template_utils.

Locks down render_playlist_config_templates behavior before cleanup.
"""

import re

from src.application.workflows.template_utils import render_playlist_config_templates


class TestRenderPlaylistConfigTemplates:
    """Tests for render_playlist_config_templates."""

    def test_track_count_replacement(self):
        """'{track_count}' is replaced with actual count."""
        config = {"name": "{track_count} tracks"}
        result = render_playlist_config_templates(config, 42)
        assert result["name"] == "42 tracks"

    def test_date_replacement(self):
        """{date} is replaced with YYYY-MM-DD format."""
        config = {"name": "Playlist {date}"}
        result = render_playlist_config_templates(config, 10)
        assert re.match(r"Playlist \d{4}-\d{2}-\d{2}", result["name"])

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

    def test_time_replacement(self):
        """{time} is replaced with HH:MM format."""
        config = {"name": "Playlist at {time}"}
        result = render_playlist_config_templates(config, 10)
        assert re.match(r"Playlist at \d{2}:\d{2}", result["name"])

    def test_datetime_replacement(self):
        """{datetime} is replaced with YYYY-MM-DD HH:MM format."""
        config = {"name": "Generated {datetime}"}
        result = render_playlist_config_templates(config, 10)
        assert re.match(r"Generated \d{4}-\d{2}-\d{2} \d{2}:\d{2}", result["name"])
