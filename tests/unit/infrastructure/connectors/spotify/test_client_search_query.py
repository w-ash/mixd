"""Tests for Spotify search query construction.

A Spotify field filter binds to a single term unless its value is quoted, so an
unquoted multi-word artist or title silently degrades into a filter on the first
word plus loose free text. These tests pin the quoting, the sanitizing of raw
GDPR-export values, and which query each mode actually puts on the wire.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.infrastructure.connectors.spotify.client import (
    SpotifyAPIClient,
    field_filtered_search_query,
    free_text_search_query,
    sanitize_search_value,
)


class TestFieldFilteredQuery:
    """Filter values are quoted so they bind whole, not term-by-term."""

    def test_multi_word_artist_and_title_are_quoted(self):
        """The v0.10.3 incident case: unquoted, this filtered on 'Robert'/'Come'."""
        assert (
            field_filtered_search_query("Robert Johnson", "Come On in My Kitchen")
            == 'artist:"Robert Johnson" track:"Come On in My Kitchen"'
        )

    def test_single_word_values_are_quoted_too(self):
        assert field_filtered_search_query("Radiohead", "Creep") == (
            'artist:"Radiohead" track:"Creep"'
        )

    def test_apostrophes_and_unicode_survive_untouched(self):
        """Both are legal inside a quoted value — normalizing them loses matches."""
        assert field_filtered_search_query("Sinéad O'Connor", "Troy") == (
            'artist:"Sinéad O\'Connor" track:"Troy"'
        )

    def test_embedded_double_quotes_are_replaced_not_escaped(self):
        """Spotify has no in-quote escape syntax; a stray quote ends the filter."""
        assert field_filtered_search_query('The "Real" Band', 'Song "Live"') == (
            'artist:"The Real Band" track:"Song Live"'
        )


class TestSanitizeFilterValue:
    """Sanitizing is quote removal plus whitespace normalization, nothing else."""

    def test_collapses_the_whitespace_a_removed_quote_leaves(self):
        assert sanitize_search_value('a "b" c') == "a b c"

    def test_leading_and_trailing_whitespace_stripped(self):
        assert sanitize_search_value("  Robert Johnson\n") == "Robert Johnson"

    def test_value_without_quotes_is_unchanged(self):
        assert sanitize_search_value("Come On in My Kitchen") == (
            "Come On in My Kitchen"
        )


class TestFreeTextQuery:
    """The widening query carries no filters and no quoting."""

    def test_artist_and_title_joined_as_plain_terms(self):
        assert (
            free_text_search_query("Robert Johnson", "Come On in My Kitchen")
            == "Robert Johnson Come On in My Kitchen"
        )

    def test_empty_artist_leaves_no_stray_whitespace(self):
        assert free_text_search_query("", "Creep") == "Creep"

    def test_embedded_double_quotes_are_replaced_here_too(self):
        """An unbalanced quote opens a phrase operator Spotify never sees closed."""
        assert free_text_search_query("Eusebe", 'Move Your Body (12" Mix)') == (
            "Eusebe Move Your Body (12 Mix)"
        )


class TestBothBuildersSanitizeAlike:
    """A quote is destructive in either shape, so neither builder may pass one on.

    The asymmetry this pins is the one that condemned living tracks: the
    filtered pass stripped the quote, the widening pass sent it, so widening
    failed on exactly the titles it exists to rescue.
    """

    ARTIST = "Eusebe"
    TITLE = 'Move Your Body (12" Mix)'

    def test_neither_query_carries_an_unbalanced_quote(self):
        filtered = field_filtered_search_query(self.ARTIST, self.TITLE)
        free_text = free_text_search_query(self.ARTIST, self.TITLE)

        assert filtered.count('"') == 4  # the four the field filters open/close
        assert '"' not in free_text

    def test_the_title_survives_intact_apart_from_the_quote(self):
        assert field_filtered_search_query(self.ARTIST, self.TITLE) == (
            'artist:"Eusebe" track:"Move Your Body (12 Mix)"'
        )
        assert free_text_search_query(self.ARTIST, self.TITLE) == (
            "Eusebe Move Your Body (12 Mix)"
        )


def _client_with_mock_transport() -> tuple[SpotifyAPIClient, AsyncMock]:
    with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
        client = SpotifyAPIClient()
    response = MagicMock()
    response.json.return_value = {"tracks": {"items": []}}
    response.raise_for_status.return_value = None
    transport = AsyncMock()
    transport.get = AsyncMock(return_value=response)
    client._client = transport
    return client, transport


class TestQueryOnTheWire:
    """The client sends its caller's query verbatim — it builds nothing itself."""

    async def test_the_field_filtered_query_reaches_the_wire_unchanged(self):
        client, transport = _client_with_mock_transport()
        query = field_filtered_search_query("Robert Johnson", "Come On in My Kitchen")

        _ = await client._search_track_impl(query)

        assert transport.get.call_args.kwargs["params"]["q"] == query

    async def test_the_free_text_query_reaches_the_wire_unchanged(self):
        client, transport = _client_with_mock_transport()
        query = free_text_search_query("Robert Johnson", "Come On in My Kitchen")

        _ = await client._search_track_impl(query)

        assert transport.get.call_args.kwargs["params"]["q"] == query
