"""Unit tests for pure track-mapper helpers.

``extract_db_artist_names`` is the JSONB ``{"names": [...]}`` boundary narrower
shared by ``TrackMapper`` and the playlist mapper. It is pure (no DB), and after
the relationship-mapping refactor it is load-bearing for the playlist mapper's
artist extraction — so its narrowing of the ``JsonValue`` union is tested here.
"""

from src.infrastructure.persistence.repositories.track.mapper import (
    extract_db_artist_names,
)


class TestExtractDbArtistNames:
    def test_extracts_string_names(self):
        assert extract_db_artist_names({"names": ["Radiohead", "Björk"]}) == [
            "Radiohead",
            "Björk",
        ]

    def test_missing_names_key_returns_empty(self):
        assert extract_db_artist_names({}) == []

    def test_names_not_a_list_returns_empty(self):
        # JsonValue union: a stray scalar under "names" must not blow up.
        assert extract_db_artist_names({"names": "Radiohead"}) == []

    def test_non_string_elements_filtered_out(self):
        # Defensive narrowing: only str elements survive.
        assert extract_db_artist_names({"names": ["ok", 123, None, "fine"]}) == [
            "ok",
            "fine",
        ]

    def test_empty_names_list_returns_empty(self):
        assert extract_db_artist_names({"names": []}) == []
