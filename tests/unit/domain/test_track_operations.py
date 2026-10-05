"""Domain layer tests for track operations and business logic.

Tests focus on track entity behavior, connector operations, and business rules.
Following TDD principles - write tests first, then implement domain services.
"""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid7

import pytest

from src.domain.entities import (
    OperationResult,
    SyncCheckpoint,
    create_lastfm_play_record,
    ensure_utc,
)
from src.domain.entities.track import (
    ArtistCredit,
    ConnectorArtistCredit,
    ConnectorTrack,
    Track,
    credits_display,
)
from tests.fixtures import TEST_USER_ID


class TestTrackEntity:
    """Test core track entity behavior and business rules."""

    def test_track_requires_at_least_one_artist(self):
        """Test that track creation fails without artists."""
        with pytest.raises(ValueError, match="Track must have at least one artist"):
            Track(title="Test Song", artists=[], user_id=TEST_USER_ID)

    def test_track_with_connector_track_id(self):
        """Test adding connector track ID."""
        track = Track(
            title="Test Song",
            artists=[ArtistCredit(credited_name="Test Artist")],
            user_id=TEST_USER_ID,
        )

        updated_track = track.with_connector_track_id(
            "spotify", "4iV5W9uYEdYUVa79Axb7Rh"
        )

        assert (
            updated_track.connector_track_identifiers["spotify"]
            == "4iV5W9uYEdYUVa79Axb7Rh"
        )
        assert updated_track != track  # Immutability check
        assert track.connector_track_identifiers == {}  # Original unchanged

    def test_track_with_multiple_connector_ids(self):
        """Test adding multiple connector IDs."""
        track = Track(
            title="Test Song",
            artists=[ArtistCredit(credited_name="Test Artist")],
            user_id=TEST_USER_ID,
        )

        track = track.with_connector_track_id("spotify", "spotify_id")
        track = track.with_connector_track_id("lastfm", "lastfm_id")

        assert track.connector_track_identifiers["spotify"] == "spotify_id"
        assert track.connector_track_identifiers["lastfm"] == "lastfm_id"

    def test_track_connector_metadata_operations(self):
        """Test connector metadata business logic."""
        track = Track(
            title="Test Song",
            artists=[ArtistCredit(credited_name="Test Artist")],
            user_id=TEST_USER_ID,
        )

        metadata = {"explicit": True, "genres": ["rock", "alternative"]}
        updated_track = track.with_connector_metadata("spotify", metadata)

        assert updated_track.get_connector_attribute("spotify", "explicit") is True
        assert updated_track.get_connector_attribute("spotify", "genres") == [
            "rock",
            "alternative",
        ]
        assert updated_track.get_connector_attribute("spotify", "nonexistent") is None
        assert (
            updated_track.get_connector_attribute("spotify", "nonexistent", "default")
            == "default"
        )

    def test_track_connector_metadata_merging(self):
        """Test that connector metadata merges correctly."""
        track = Track(
            title="Test Song",
            artists=[ArtistCredit(credited_name="Test Artist")],
            user_id=TEST_USER_ID,
        )

        # Add initial metadata
        track = track.with_connector_metadata("spotify", {"explicit": True})

        # Add more metadata - should merge, not replace
        track = track.with_connector_metadata("spotify", {"genres": ["rock"]})

        assert track.get_connector_attribute("spotify", "explicit") is True
        assert track.get_connector_attribute("spotify", "genres") == ["rock"]


class TestArtistCredit:
    """Credit value object: validation and the optional identity fields."""

    def test_credited_name_must_be_str(self):
        with pytest.raises(TypeError):
            ArtistCredit(credited_name=cast(str, 123))

    def test_artist_id_must_be_uuid(self):
        with pytest.raises(TypeError):
            ArtistCredit(credited_name="X", artist_id=cast(UUID, "not-a-uuid"))


class TestCreditsDisplay:
    """The display string is also the ``artists_text`` column value."""

    def test_single_credit_is_its_name(self):
        assert credits_display([ArtistCredit(credited_name="Radiohead")]) == "Radiohead"

    def test_default_separator_is_comma_space(self):
        credits = [ArtistCredit(credited_name="A"), ArtistCredit(credited_name="B")]
        assert credits_display(credits) == "A, B"

    def test_join_phrase_replaces_default_separator(self):
        credits = [
            ArtistCredit(credited_name="Thom Yorke", join_phrase=" & "),
            ArtistCredit(credited_name="PJ Harvey"),
        ]
        assert credits_display(credits) == "Thom Yorke & PJ Harvey"

    def test_join_phrases_mix_with_default(self):
        credits = [
            ArtistCredit(credited_name="A", join_phrase=" feat. "),
            ArtistCredit(credited_name="B"),
            ArtistCredit(credited_name="C"),
        ]
        assert credits_display(credits) == "A feat. B, C"

    def test_last_credits_join_phrase_is_never_trailing(self):
        credits = [
            ArtistCredit(credited_name="A"),
            ArtistCredit(credited_name="B", join_phrase=" & "),
        ]
        assert credits_display(credits) == "A, B"

    def test_empty_join_phrase_falls_back_to_default(self):
        credits = [
            ArtistCredit(credited_name="A", join_phrase=""),
            ArtistCredit(credited_name="B"),
        ]
        assert credits_display(credits) == "A, B"

    def test_empty_sequence_is_empty_string(self):
        assert credits_display(()) == ""

    def test_track_and_connector_track_delegate(self):
        """One implementation displays canonical and connector credits alike."""
        track = Track(
            title="T",
            artists=[
                ArtistCredit(credited_name="A", join_phrase=" x "),
                ArtistCredit(credited_name="B"),
            ],
            user_id=TEST_USER_ID,
        )
        connector_track = ConnectorTrack(
            "spotify",
            "sp1",
            "T",
            [
                ConnectorArtistCredit(credited_name="A", join_phrase=" x "),
                ConnectorArtistCredit(credited_name="B"),
            ],
        )
        assert track.artists_display == "A x B"
        assert connector_track.artists_display == "A x B"


class TestConnectorArtistCredit:
    """A service's own credit: its artist id is the service's, never a canonical one."""

    def test_connector_track_rejects_canonical_credits(self):
        with pytest.raises(TypeError, match="Expected ConnectorArtistCredit"):
            ConnectorTrack("spotify", "sp1", "T", [ArtistCredit(credited_name="A")])


class TestTrackCredits:
    """``Track.artists`` / ``ConnectorTrack.artists`` are tuples of credits."""

    def test_list_literal_is_converted_to_tuple(self):
        credit = ArtistCredit(credited_name="A")
        track = Track(title="T", artists=[credit], user_id=TEST_USER_ID)
        assert track.artists == (credit,)

    def test_connector_track_list_literal_is_converted_to_tuple(self):
        credit = ConnectorArtistCredit(credited_name="A")
        connector_track = ConnectorTrack("spotify", "sp1", "T", [credit])
        assert connector_track.artists == (credit,)

    def test_non_credit_element_is_rejected(self):
        with pytest.raises(TypeError, match="Expected ArtistCredit"):
            Track(
                title="T", artists=cast(list[ArtistCredit], ["A"]), user_id=TEST_USER_ID
            )


class TestSyncCheckpoint:
    """Test SyncCheckpoint entity behavior."""

    def test_sync_checkpoint_creation_and_update(self):
        """Test SyncCheckpoint creation and immutable updates."""
        checkpoint = SyncCheckpoint(
            user_id="user123", service="spotify", entity_type="likes"
        )

        assert checkpoint.user_id == "user123"
        assert checkpoint.service == "spotify"
        assert checkpoint.entity_type == "likes"

        # Test update returns new instance
        timestamp = datetime.now(UTC)
        updated = checkpoint.with_update(timestamp, "cursor123")
        assert updated.last_timestamp == timestamp
        assert updated.cursor == "cursor123"
        assert checkpoint.last_timestamp is None  # Original unchanged

    def test_with_update_preserves_cursor_when_omitted(self):
        """Omitting cursor arg preserves the existing cursor value."""
        checkpoint = SyncCheckpoint(
            user_id="u", service="s", entity_type="likes", cursor="page2"
        )
        updated = checkpoint.with_update(datetime.now(UTC))
        assert updated.cursor == "page2"

    def test_with_update_clears_cursor_with_none(self):
        """Passing cursor=None explicitly clears a stored cursor."""
        checkpoint = SyncCheckpoint(
            user_id="u", service="s", entity_type="likes", cursor="page2"
        )
        updated = checkpoint.with_update(datetime.now(UTC), cursor=None)
        assert updated.cursor is None

    def test_with_update_replaces_cursor(self):
        """Passing a new cursor string replaces the old one."""
        checkpoint = SyncCheckpoint(
            user_id="u", service="s", entity_type="likes", cursor="page2"
        )
        updated = checkpoint.with_update(datetime.now(UTC), cursor="page3")
        assert updated.cursor == "page3"


class TestPlayRecord:
    """Test PlayRecord and factory functions."""

    def test_create_lastfm_play_record(self):
        """Test LastFM play record creation factory function."""
        scrobbled_at = datetime.now(UTC)
        record = create_lastfm_play_record(
            artist_name="Artist",
            track_name="Song",
            scrobbled_at=scrobbled_at,
            album_name="Album",
            lastfm_track_url="https://last.fm/track/123",
            mbid="123-456-789",
            loved=True,
        )

        assert record.artist_name == "Artist"
        assert record.track_name == "Song"
        assert record.played_at == scrobbled_at
        assert record.service == "lastfm"
        assert record.album_name == "Album"
        assert (
            record.service_metadata["lastfm_track_url"] == "https://last.fm/track/123"
        )
        assert record.service_metadata["mbid"] == "123-456-789"
        assert record.service_metadata["loved"] is True


class TestOperationResultEntity:
    """Test OperationResult behavior."""

    def test_operation_result_per_track_metrics(self):
        """Test OperationResult per-track metric access."""
        artist = ArtistCredit(credited_name="Artist")
        track1 = Track(title="Song 1", artists=[artist], user_id=TEST_USER_ID)
        track2 = Track(title="Song 2", artists=[artist], user_id=TEST_USER_ID)
        tracks = [track1, track2]

        result = OperationResult(
            tracks=tracks, operation_name="test_operation", execution_time=1.5
        )

        missing_id = uuid7()
        result.metrics["status"] = {
            track1.id: "processed",
            track2.id: "processed",
        }
        assert result.get_metric(track1.id, "status") == "processed"
        assert result.get_metric(missing_id, "status", "not_found") == "not_found"


class TestEnsureUtc:
    """Test UTC timezone enforcement utility."""

    def test_ensure_utc_none_input(self):
        """Test None passes through."""
        assert ensure_utc(None) is None

    def test_ensure_utc_naive_datetime(self):
        """Test naive datetime is converted to UTC."""
        naive_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=None)  # ruff:ignore[call-datetime-without-tzinfo]
        utc_dt = ensure_utc(naive_dt)
        # The wall-clock reading is kept and labelled UTC, not shifted from local time.
        assert utc_dt == datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert utc_dt.tzinfo == UTC

    def test_ensure_utc_already_utc(self):
        """Test already-UTC datetime passes through unchanged."""
        already_utc = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = ensure_utc(already_utc)
        assert result == already_utc
