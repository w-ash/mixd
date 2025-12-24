"""Integration tests for Last.FM intelligent track lookup with fallback strategies.

Tests verify:
1. MBID → artist/title fallback behavior
2. Multi-artist fallback strategy
3. Comprehensive logging at each step
4. Connection error vs not-found error handling

All tests use mocked Last.FM API calls to avoid network dependencies.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pylast

from src.domain.entities import Artist, Track
from src.infrastructure.connectors.lastfm.operations import LastFMOperations


@pytest.fixture
def lastfm_operations():
    """Create Last.FM operations instance with mocked client."""
    mock_client = MagicMock()
    return LastFMOperations(client=mock_client)


@pytest.fixture
def sample_track():
    """Create a sample track for testing."""
    return Track(
        id=1,
        title="Test Song",
        artists=[Artist(name="Test Artist")],
    )


@pytest.fixture
def multi_artist_track():
    """Create a multi-artist track for testing."""
    return Track(
        id=2,
        title="Collaboration",
        artists=[
            Artist(name="Artist One"),
            Artist(name="Artist Two"),
            Artist(name="Artist Three"),
        ],
    )


# -------------------------------------------------------------------------
# MBID Fallback Tests (4 tests)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mbid_lookup_succeeds_no_fallback_needed(
    lastfm_operations, sample_track
):
    """Test MBID lookup succeeds on first try, no artist/title fallback needed.

    Given: Track with valid MBID
    When: get_track_info_intelligent() called
    Then: MBID lookup succeeds, returns track
    Assert: MBID method called, artist/title NOT called
    """
    # Setup: Add MBID to track
    track_with_mbid = sample_track.with_connector_metadata(
        "lastfm", {"lastfm_mbid": "test-mbid-123"}
    )

    # Mock successful MBID lookup at client level
    mock_track_data = {
        "lastfm_title": "Test Song",
        "lastfm_artist_name": "Test Artist",
        "lastfm_url": "https://last.fm/music/test",
    }
    lastfm_operations.client.get_track_info_comprehensive_by_mbid = AsyncMock(
        return_value=mock_track_data
    )

    # Execute
    result = await lastfm_operations.get_track_info_intelligent(track_with_mbid)

    # Assert result
    assert result is not None
    assert result.lastfm_title == "Test Song"

    # Assert MBID method was called (successful first try)
    assert lastfm_operations.client.get_track_info_comprehensive_by_mbid.called
    # Assert artist/title method was NOT called (no fallback needed)
    assert not lastfm_operations.client.get_track_info_comprehensive.called


@pytest.mark.asyncio
async def test_mbid_fails_fallback_to_artist_title_succeeds(
    lastfm_operations, sample_track
):
    """Test MBID lookup fails, artist/title fallback succeeds.

    Given: Track with MBID that doesn't exist, valid artist/title
    When: get_track_info_intelligent() called
    Then: MBID fails, artist/title succeeds
    Assert: Both methods called, result from artist/title
    """
    # Setup: Add MBID to track
    track_with_mbid = sample_track.with_connector_metadata(
        "lastfm", {"lastfm_mbid": "nonexistent-mbid"}
    )

    # Mock MBID lookup failure (returns None)
    lastfm_operations.client.get_track_info_comprehensive_by_mbid = AsyncMock(
        return_value=None
    )

    # Mock successful artist/title lookup
    mock_track_data = {
        "lastfm_title": "Test Song",
        "lastfm_artist_name": "Test Artist",
        "lastfm_url": "https://last.fm/music/test",
    }
    lastfm_operations.client.get_track_info_comprehensive = AsyncMock(
        return_value=mock_track_data
    )

    # Execute
    result = await lastfm_operations.get_track_info_intelligent(track_with_mbid)

    # Assert result from artist/title fallback
    assert result is not None
    assert result.lastfm_title == "Test Song"

    # Assert both methods called
    assert lastfm_operations.client.get_track_info_comprehensive_by_mbid.called
    assert lastfm_operations.client.get_track_info_comprehensive.called


@pytest.mark.asyncio
async def test_no_mbid_goes_straight_to_artist_title(
    lastfm_operations, sample_track
):
    """Test track with no MBID goes straight to artist/title lookup.

    Given: Track with no MBID, valid artist/title
    When: get_track_info_intelligent() called
    Then: Goes straight to artist/title (no MBID attempt)
    Assert: MBID method NOT called, artist/title called
    """
    # Setup: Track has no MBID (sample_track already has empty connector_metadata)

    # Mock successful artist/title lookup
    mock_track_data = {
        "lastfm_title": "Test Song",
        "lastfm_artist_name": "Test Artist",
        "lastfm_url": "https://last.fm/music/test",
    }
    lastfm_operations.client.get_track_info_comprehensive = AsyncMock(
        return_value=mock_track_data
    )

    # Execute
    result = await lastfm_operations.get_track_info_intelligent(sample_track)

    # Assert result
    assert result is not None
    assert result.lastfm_title == "Test Song"

    # Assert MBID method NOT called (no MBID available)
    assert not lastfm_operations.client.get_track_info_comprehensive_by_mbid.called
    # Assert artist/title method called
    assert lastfm_operations.client.get_track_info_comprehensive.called


@pytest.mark.asyncio
async def test_both_mbid_and_artist_title_fail(
    lastfm_operations, sample_track
):
    """Test both MBID and artist/title lookups fail, returns empty.

    Given: Track with non-existent MBID and artist/title
    When: get_track_info_intelligent() called
    Then: Both attempts fail, returns empty
    Assert: Both methods called, empty result
    """
    # Setup: Add MBID to track
    track_with_mbid = sample_track.with_connector_metadata(
        "lastfm", {"lastfm_mbid": "nonexistent-mbid"}
    )

    # Mock both lookups failing
    lastfm_operations.client.get_track_info_comprehensive_by_mbid = AsyncMock(
        return_value=None
    )
    lastfm_operations.client.get_track_info_comprehensive = AsyncMock(
        return_value=None
    )

    # Execute
    result = await lastfm_operations.get_track_info_intelligent(track_with_mbid)

    # Assert result is empty
    assert result is not None
    assert result.lastfm_title is None  # Empty result

    # Assert both methods called
    assert lastfm_operations.client.get_track_info_comprehensive_by_mbid.called
    assert lastfm_operations.client.get_track_info_comprehensive.called


# -------------------------------------------------------------------------
# Multi-Artist Fallback Tests (4 tests)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_artist_first_artist_succeeds(
    lastfm_operations, multi_artist_track
):
    """Test multi-artist track where first artist matches.

    Given: Track with 3 artists, first artist matches on Last.FM
    When: get_track_info_intelligent() called
    Then: First artist succeeds, no fallback needed
    Assert: Only called once with first artist
    """
    # Mock first artist succeeds
    mock_track_data = {
        "lastfm_title": "Collaboration",
        "lastfm_artist_name": "Artist One",
        "lastfm_url": "https://last.fm/music/test",
    }
    mock_get_track_info = AsyncMock(return_value=mock_track_data)
    lastfm_operations.client.get_track_info_comprehensive = mock_get_track_info

    # Execute
    result = await lastfm_operations.get_track_info_intelligent(multi_artist_track)

    # Assert result
    assert result is not None
    assert result.lastfm_title == "Collaboration"
    assert result.lastfm_artist_name == "Artist One"

    # Assert only called once (first artist)
    assert mock_get_track_info.call_count == 1
    mock_get_track_info.assert_called_once_with("Artist One", "Collaboration")


@pytest.mark.asyncio
async def test_multi_artist_second_artist_succeeds(
    lastfm_operations, multi_artist_track
):
    """Test multi-artist track where second artist matches.

    Given: Track with 3 artists, only second artist matches
    When: get_track_info_intelligent() called
    Then: First artist fails, second artist succeeds
    Assert: Called twice (first failed, second succeeded)
    """
    # Mock first artist fails, second succeeds
    call_count = 0

    async def mock_get_track_info_func(artist: str, title: str):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First artist fails
            return None
        # Second artist succeeds
        return {
            "lastfm_title": "Collaboration",
            "lastfm_artist_name": artist,
            "lastfm_url": "https://last.fm/music/test",
        }

    mock_get_track_info = AsyncMock(side_effect=mock_get_track_info_func)
    lastfm_operations.client.get_track_info_comprehensive = mock_get_track_info

    # Execute
    result = await lastfm_operations.get_track_info_intelligent(multi_artist_track)

    # Assert result
    assert result is not None
    assert result.lastfm_title == "Collaboration"
    assert result.lastfm_artist_name == "Artist Two"

    # Assert called twice (first and second artist)
    assert mock_get_track_info.call_count == 2


@pytest.mark.asyncio
async def test_multi_artist_all_fail(
    lastfm_operations, multi_artist_track
):
    """Test multi-artist track where all artists fail.

    Given: Track with 3 artists, none match on Last.FM
    When: get_track_info_intelligent() called
    Then: All 3 artists tried, all fail
    Assert: Called 3 times with all artists
    """
    # Mock all artists fail
    mock_get_track_info = AsyncMock(return_value=None)
    lastfm_operations.client.get_track_info_comprehensive = mock_get_track_info

    # Execute
    result = await lastfm_operations.get_track_info_intelligent(multi_artist_track)

    # Assert result is empty
    assert result is not None
    assert result.lastfm_title is None

    # Assert called 3 times (all artists)
    assert mock_get_track_info.call_count == 3

    # Assert all three calls happened
    calls = mock_get_track_info.call_args_list
    assert calls[0][0] == ("Artist One", "Collaboration")
    assert calls[1][0] == ("Artist Two", "Collaboration")
    assert calls[2][0] == ("Artist Three", "Collaboration")


@pytest.mark.asyncio
async def test_single_artist_no_multi_artist_fallback(
    lastfm_operations, sample_track
):
    """Test single-artist track that doesn't match (no multi-artist fallback).

    Given: Track with 1 artist that doesn't match
    When: get_track_info_intelligent() called
    Then: Single artist attempt fails, no fallback (nothing to fall back to)
    Assert: Called once with single artist
    """
    # Mock artist fails
    mock_get_track_info = AsyncMock(return_value=None)
    lastfm_operations.client.get_track_info_comprehensive = mock_get_track_info

    # Execute
    result = await lastfm_operations.get_track_info_intelligent(sample_track)

    # Assert result is empty
    assert result is not None
    assert result.lastfm_title is None

    # Assert called once
    assert mock_get_track_info.call_count == 1


# -------------------------------------------------------------------------
# Error Handling Tests (3 tests)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_error_falls_back_to_artist_title(
    lastfm_operations, sample_track
):
    """Test connection error on MBID lookup falls back to artist/title.

    Given: MBID lookup raises temporary network error
    When: get_track_info_intelligent() called
    Then: Error caught, falls back to artist/title which succeeds
    Assert: Both methods called, artist/title returns result
    """
    # Setup: Add MBID to track
    track_with_mbid = sample_track.with_connector_metadata(
        "lastfm", {"lastfm_mbid": "test-mbid-123"}
    )

    # Mock MBID lookup raises connection error (caught by try/except in operations.py)
    lastfm_operations.client.get_track_info_comprehensive_by_mbid = AsyncMock(
        side_effect=pylast.WSError("lastfm", "11", "Service Offline - Try again later")
    )

    # Mock artist/title fallback succeeds
    mock_track_data = {
        "lastfm_title": "Test Song",
        "lastfm_artist_name": "Test Artist",
        "lastfm_url": "https://last.fm/music/test",
    }
    lastfm_operations.client.get_track_info_comprehensive = AsyncMock(
        return_value=mock_track_data
    )

    # Execute - should fall back to artist/title
    result = await lastfm_operations.get_track_info_intelligent(track_with_mbid)

    # Assert result from artist/title fallback
    assert result is not None
    assert result.lastfm_title == "Test Song"

    # Assert both methods called
    assert lastfm_operations.client.get_track_info_comprehensive_by_mbid.called
    assert lastfm_operations.client.get_track_info_comprehensive.called


@pytest.mark.asyncio
async def test_connection_error_propagates_when_no_fallback(
    lastfm_operations, sample_track
):
    """Test connection error propagates when no fallback available.

    Given: Artist/title lookup raises connection error (no MBID to fall back to)
    When: get_track_info_intelligent() called
    Then: Error caught, returns empty result (per operations.py try/except)
    """
    # Mock artist/title lookup raises connection error
    lastfm_operations.client.get_track_info_comprehensive = AsyncMock(
        side_effect=pylast.WSError("lastfm", "11", "Service Offline - Try again later")
    )

    # Execute - error caught by try/except in get_track_info()
    result = await lastfm_operations.get_track_info_intelligent(sample_track)

    # Assert empty result (error was caught and logged)
    assert result is not None
    assert result.lastfm_title is None


@pytest.mark.asyncio
async def test_not_found_error_tries_next_artist(
    lastfm_operations, multi_artist_track
):
    """Test track-not-found doesn't stop iteration, tries next artist.

    Given: Multi-artist track, first artist returns None (not found)
    When: get_track_info_intelligent() called
    Then: Tries second artist, succeeds
    Assert: Called twice (first failed, second succeeded)
    """
    # Mock first artist returns None (not found), second succeeds
    call_count = 0

    async def mock_get_track_info_func(artist: str, title: str):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First artist not found
            return None
        # Second artist succeeds
        return {
            "lastfm_title": "Collaboration",
            "lastfm_artist_name": artist,
            "lastfm_url": "https://last.fm/music/test",
        }

    mock_get_track_info = AsyncMock(side_effect=mock_get_track_info_func)
    lastfm_operations.client.get_track_info_comprehensive = mock_get_track_info

    # Execute
    result = await lastfm_operations.get_track_info_intelligent(multi_artist_track)

    # Assert result from second artist
    assert result is not None
    assert result.lastfm_title == "Collaboration"

    # Assert called twice (first failed, second succeeded)
    assert mock_get_track_info.call_count == 2
