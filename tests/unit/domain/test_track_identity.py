"""Unit tests for Track identity resolution - complex business logic tests.

Tests the core business rules for determining when two tracks represent the same song,
which is critical for deduplication, merging, and cross-service track linking.
"""

from src.domain.entities import ArtistCredit, Track
from tests.fixtures import TEST_USER_ID


class TestTrackIdentityResolution:
    """Test Track.has_same_identity_as method - complex business logic with no external dependencies.

    This tests the core deduplication logic that determines when tracks from different
    sources should be considered the same song. Critical for preventing duplicate
    imports and enabling cross-service linking.
    """

    def test_isrc_takes_priority_over_metadata_differences(self):
        """Test that ISRC match overrides title/artist differences - key business rule."""
        # Real scenario: Same song from different releases/remasters
        original_release = Track(
            title="Paranoid Android",
            artists=[ArtistCredit(credited_name="Radiohead")],
            album="OK Computer",
            isrc="GBUM71505078",
            user_id=TEST_USER_ID,
        )
        remaster_release = Track(
            title="Paranoid Android - Remastered",
            artists=[ArtistCredit(credited_name="Radiohead")],
            album="OK Computer (2017 Remaster)",
            isrc="GBUM71505078",
            user_id=TEST_USER_ID,  # Same ISRC - same song!
        )

        # Should be considered identical despite different title/album
        assert original_release.has_same_identity_as(remaster_release)
        assert remaster_release.has_same_identity_as(original_release)

    def test_connector_id_matching_with_multiple_services(self):
        """Test track matching via shared connector IDs across services."""
        # Real scenario: Track imported from Spotify, then found on Last.fm
        spotify_track = Track(
            title="Karma Police",
            artists=[ArtistCredit(credited_name="Radiohead")],
            connector_track_identifiers={
                "spotify": "63OQupATfueTdZMWTxzEle",
                "musicbrainz": "8b2b6471-7903-4f84-8b3f-d1d2e7c4b9a8",
            },
            user_id=TEST_USER_ID,
        )
        lastfm_track = Track(
            title="Karma Police",  # Same title
            artists=[ArtistCredit(credited_name="Radiohead")],
            connector_track_identifiers={
                "lastfm": "track_123456",
                "musicbrainz": "8b2b6471-7903-4f84-8b3f-d1d2e7c4b9a8",  # Same MusicBrainz ID
            },
            user_id=TEST_USER_ID,
        )

        # Should match via shared MusicBrainz ID
        assert spotify_track.has_same_identity_as(lastfm_track)

    def test_no_match_with_completely_different_identifiers(self):
        """Test that tracks with no shared identifiers are not considered identical."""
        track1 = Track(
            title="Paranoid Android",
            artists=[ArtistCredit(credited_name="Radiohead")],
            isrc="GBUM71505078",
            connector_track_identifiers={"spotify": "63OQupATfueTdZMWTxzEle"},
            user_id=TEST_USER_ID,
        )
        track2 = Track(
            title="Yesterday",  # Different song entirely
            artists=[ArtistCredit(credited_name="The Beatles")],
            isrc="USRC17607839",  # Different ISRC
            connector_track_identifiers={"spotify": "3BxWKCI06eQ5Od8TY2JBeA"},
            user_id=TEST_USER_ID,  # Different Spotify ID
        )

        assert not track1.has_same_identity_as(track2)
        assert not track2.has_same_identity_as(track1)

    def test_partial_connector_overlap_still_matches(self):
        """Test that ANY shared connector ID creates a match - business rule."""
        # Real scenario: Track with multiple IDs, partial overlap should still match
        track_with_many_ids = Track(
            title="Creep",
            artists=[ArtistCredit(credited_name="Radiohead")],
            connector_track_identifiers={
                "spotify": "70LcF31zb1H0PyJoS1Sx1r",
                "lastfm": "track_789012",
                "apple_music": "am_345678",
                "youtube": "yt_901234",
            },
            user_id=TEST_USER_ID,
        )
        track_with_one_matching_id = Track(
            title="Creep (Radio Edit)",  # Different title variation
            artists=[ArtistCredit(credited_name="Radiohead")],
            connector_track_identifiers={
                "spotify": "70LcF31zb1H0PyJoS1Sx1r",  # Same Spotify ID - enough for match!
                "tidal": "tidal_567890",  # Different other service
            },
            user_id=TEST_USER_ID,
        )

        # Should match because of shared Spotify ID
        assert track_with_many_ids.has_same_identity_as(track_with_one_matching_id)

    def test_blank_identifiers_never_match(self):
        """An empty ISRC or connector id is absent data, not a shared identity."""
        track1 = Track(
            title="Test Song",
            artists=[ArtistCredit(credited_name="Test Artist")],
            isrc="",
            connector_track_identifiers={"spotify": ""},
            user_id=TEST_USER_ID,
        )
        track2 = Track(
            title="Other Song",
            artists=[ArtistCredit(credited_name="Other Artist")],
            isrc="",
            connector_track_identifiers={"spotify": ""},
            user_id=TEST_USER_ID,
        )

        assert not track1.has_same_identity_as(track2)

    def test_case_sensitive_connector_ids(self):
        """Test that connector IDs are case-sensitive - important for exact matching."""
        track1 = Track(
            title="Test Song",
            artists=[ArtistCredit(credited_name="Test Artist")],
            connector_track_identifiers={"spotify": "4iV5W9uYEdYUVa79Axb7Rh"},
            user_id=TEST_USER_ID,
        )
        track2 = Track(
            title="Test Song",
            artists=[ArtistCredit(credited_name="Test Artist")],
            connector_track_identifiers={"spotify": "4iv5w9uyedyuva79axb7rh"},
            user_id=TEST_USER_ID,  # Different case
        )

        # Case differences should prevent matching (Spotify IDs are case-sensitive)
        assert not track1.has_same_identity_as(track2)

    def test_isrc_priority_over_connector_ids(self):
        """Test that ISRC matching takes precedence over connector ID conflicts."""
        # Edge case: Different connector IDs but same ISRC (data integrity issue)
        track1 = Track(
            title="Test Song",
            artists=[ArtistCredit(credited_name="Test Artist")],
            isrc="USUM71703861",  # Same ISRC
            connector_track_identifiers={"spotify": "different_id_1"},
            user_id=TEST_USER_ID,
        )
        track2 = Track(
            title="Test Song",
            artists=[ArtistCredit(credited_name="Test Artist")],
            isrc="USUM71703861",  # Same ISRC - this should win
            connector_track_identifiers={"spotify": "different_id_2"},
            user_id=TEST_USER_ID,  # Different Spotify ID
        )

        # ISRC match should override connector ID mismatch
        assert track1.has_same_identity_as(track2)

    def test_tracks_with_no_external_identifiers(self):
        """Test tracks with only title/artist metadata - should not match."""
        # Real scenario: Tracks from different imports with no external IDs
        track1 = Track(
            title="Unknown Song",
            artists=[ArtistCredit(credited_name="Unknown Artist")],
            user_id=TEST_USER_ID,
            # No ISRC, no connector IDs
        )
        track2 = Track(
            title="Unknown Song",  # Same title/artist
            artists=[ArtistCredit(credited_name="Unknown Artist")],
            user_id=TEST_USER_ID,
            # No ISRC, no connector IDs
        )

        # Without external identifiers, cannot determine if same song
        assert not track1.has_same_identity_as(track2)
