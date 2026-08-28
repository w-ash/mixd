"""Tests for AppleMusicMatchingProvider — conservative ISRC-only matching.

Validates the provider's ISRC batch lookup (chunking delegated to the client),
the ``IsrcOnly`` strategy contract (no artist/title funnel, ever), the
no-storefront failure path, and the failed-chunk path (unanswered ISRCs fail
as API_ERROR, never NO_RESULTS).
"""

from unittest.mock import AsyncMock, patch

from src.domain.entities import Artist, Track
from src.domain.matching.types import MatchFailureReason
from src.infrastructure.connectors._shared.matching_provider import IsrcOnly
from src.infrastructure.connectors.apple_music.client import CatalogSongsLookup
from src.infrastructure.connectors.apple_music.matching_provider import (
    AppleMusicMatchingProvider,
)
from tests.fixtures import make_apple_song

STOREFRONT_PATCH = (
    "src.infrastructure.connectors.apple_music.matching_provider.resolve_storefront"
)


def _make_provider(songs=None, failed_values=None, storefront="us"):
    """Provider over a fully mocked client; returns (provider, client)."""
    client = AsyncMock()
    client.get_songs_by_isrc.return_value = CatalogSongsLookup(
        songs=songs or [], failed_values=failed_values or []
    )
    provider = AppleMusicMatchingProvider(client)
    return provider, client, storefront


def _isrc_track(isrc: str = "USUM72309818", title: str = "Test Song") -> Track:
    return Track(title=title, isrc=isrc, artists=[Artist(name="Test Artist")])


class TestServiceContract:
    def test_service_name_is_apple(self):
        provider, _, _ = _make_provider()
        assert provider.service_name == "apple"

    def test_strategy_is_isrc_only(self):
        """No artist/title phase exists — the strategy has no such hook."""
        provider, _, _ = _make_provider()
        assert isinstance(provider._match_strategy(), IsrcOnly)


class TestIsrcMatching:
    async def test_isrc_hit_returns_raw_match(self):
        song = make_apple_song(song_id="1613600188", isrc="USUM72309818")
        provider, client, _ = _make_provider(songs=[song])
        track = _isrc_track("USUM72309818")

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result = await provider.fetch_raw_matches_for_tracks([track])

        assert track.id in result.matches
        match = result.matches[track.id]
        assert match["connector_id"] == "1613600188"
        # "isrc" (not "isrc_match"): ISRC_GRADE_METHODS keys confidence
        # scoring on the raw-match method string Spotify also emits.
        assert match["match_method"] == "isrc"
        assert match["service_data"]["title"] == "Test Song"
        assert match["service_data"]["artist"] == "Test Artist"
        assert match["service_data"]["album"] == "Test Album"
        assert match["service_data"]["duration_ms"] == 200_000
        assert match["service_data"]["isrc"] == "USUM72309818"
        assert match["service_data"]["release_date"] == "2023-01-01"
        client.get_songs_by_isrc.assert_awaited_once_with("us", ["USUM72309818"])

    async def test_chunking_delegated_to_client_single_call(self):
        """One client call for the whole batch — the client owns chunking."""
        tracks = [_isrc_track(f"USUM723098{i:02d}", f"Song {i}") for i in range(30)]
        songs = [
            make_apple_song(song_id=str(i), isrc=f"USUM723098{i:02d}")
            for i in range(30)
        ]
        provider, client, _ = _make_provider(songs=songs)

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result = await provider.fetch_raw_matches_for_tracks(tracks)

        client.get_songs_by_isrc.assert_awaited_once()
        assert len(result.matches) == 30

    async def test_isrc_miss_fails_without_artist_title_fallback(self):
        """ISRC miss → NO_RESULTS failure; no second-chance search runs."""
        provider, client, _ = _make_provider(songs=[])
        track = _isrc_track("USUM72309818")

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result = await provider.fetch_raw_matches_for_tracks([track])

        assert not result.matches
        assert len(result.failures) == 1
        failure = result.failures[0]
        assert failure.track_id == track.id
        assert failure.reason == MatchFailureReason.NO_RESULTS
        assert failure.method == "isrc"
        assert failure.service == "apple"
        client.get_songs_by_isrc.assert_awaited_once()

    async def test_track_without_isrc_fails_without_artist_title_call(self):
        """No ISRC → NO_ISRC failure from the IsrcOnly strategy, no lookup."""
        provider, client, _ = _make_provider()
        track = Track(title="No Code", artists=[Artist(name="Someone")])

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result = await provider.fetch_raw_matches_for_tracks([track])

        assert not result.matches
        assert len(result.failures) == 1
        assert result.failures[0].reason == MatchFailureReason.NO_ISRC
        client.get_songs_by_isrc.assert_not_awaited()

    async def test_no_storefront_fails_batch_cleanly(self):
        """Storefront unavailable → per-track MatchFailures, no API call."""
        provider, client, _ = _make_provider()
        tracks = [_isrc_track("USUM72309818"), _isrc_track("USUM72309819", "Other")]

        with patch(STOREFRONT_PATCH, AsyncMock(return_value=None)):
            result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert not result.matches
        assert len(result.failures) == 2
        assert all(
            f.reason == MatchFailureReason.API_ERROR and f.method == "isrc"
            for f in result.failures
        )
        client.get_songs_by_isrc.assert_not_awaited()

    async def test_failed_chunk_isrcs_fail_as_api_error_not_no_results(self):
        """A chunk that failed after retries left its ISRCs UNANSWERED:
        recording NO_RESULTS would poison later re-matching with a false
        'absent from catalog' verdict."""
        failed_tracks = [
            _isrc_track(f"USUM723098{i:02d}", f"Failed {i}") for i in range(25)
        ]
        matched_track = _isrc_track("USUM72309918", "Matched")
        absent_track = _isrc_track("USUM72309919", "Absent")
        provider, _, _ = _make_provider(
            songs=[make_apple_song(song_id="ok-1", isrc="USUM72309918")],
            failed_values=[f"USUM723098{i:02d}" for i in range(25)],
        )

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result = await provider.fetch_raw_matches_for_tracks([
                *failed_tracks,
                matched_track,
                absent_track,
            ])

        assert set(result.matches) == {matched_track.id}
        by_reason = {}
        for failure in result.failures:
            by_reason.setdefault(failure.reason, []).append(failure)
        assert len(by_reason[MatchFailureReason.API_ERROR]) == 25
        assert {f.track_id for f in by_reason[MatchFailureReason.API_ERROR]} == {
            t.id for t in failed_tracks
        }
        # 25 failures from ONE failed request: the message must say so, and name
        # the code it was actually looking up.
        chunk_failure = by_reason[MatchFailureReason.API_ERROR][0]
        assert "the chunk holding ISRC" in chunk_failure.details
        assert "USUM72309800" in chunk_failure.details
        # The absent ISRC sat in a SUCCESSFUL chunk: genuinely no results.
        assert [f.track_id for f in by_reason[MatchFailureReason.NO_RESULTS]] == [
            absent_track.id
        ]

    async def test_all_chunks_failed_fails_every_track_as_api_error(self):
        provider, _, _ = _make_provider(
            songs=[], failed_values=["USUM72309818", "USUM72309819"]
        )
        tracks = [_isrc_track("USUM72309818"), _isrc_track("USUM72309819", "Other")]

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert not result.matches
        assert [f.reason for f in result.failures] == [
            MatchFailureReason.API_ERROR,
            MatchFailureReason.API_ERROR,
        ]

    async def test_hyphenated_isrc_normalized_for_correlation(self):
        """Track ISRCs and Apple's response ISRCs correlate normalized."""
        song = make_apple_song(song_id="42", isrc="USUM72309818")
        provider, _, _ = _make_provider(songs=[song])
        track = _isrc_track("US-UM7-23-09818")

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result = await provider.fetch_raw_matches_for_tracks([track])

        assert track.id in result.matches
