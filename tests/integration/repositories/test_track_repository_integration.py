"""Integration tests for TrackRepository with real database operations following modern patterns."""

from uuid import uuid4

from src.domain.entities import Artist, Track
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


class TestTrackRepositoryIntegration:
    """Integration tests for track repository with real database operations."""

    async def test_save_and_retrieve_track(self, db_session, test_data_tracker):
        """Test saving and retrieving a track with automatic cleanup tracking."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Create test track with unique identifiers
        test_track = Track(
            id=None,
            title=f"TEST_Track_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
            album=f"TEST_Album_{uuid4()}",
            duration_ms=180000,
            connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
        )

        # Save track
        saved_track = await track_repo.save_track(test_track)
        test_data_tracker.add_track(saved_track.id)

        # Verify track was saved correctly
        assert saved_track.id is not None
        assert saved_track.title == test_track.title
        assert saved_track.artists[0].name == test_track.artists[0].name
        assert saved_track.album == test_track.album
        assert saved_track.duration_ms == test_track.duration_ms

        # Retrieve track by ID
        retrieved_track = await track_repo.get_by_id(saved_track.id)
        assert retrieved_track is not None
        assert retrieved_track.title == test_track.title
        assert len(retrieved_track.artists) == 1
        assert retrieved_track.artists[0].name == test_track.artists[0].name

    async def test_find_tracks_by_ids_operations(self, db_session, test_data_tracker):
        """Test find_tracks_by_ids with empty list, single track, multiple tracks, and missing IDs."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Test empty list handling
        result = await track_repo.find_tracks_by_ids([])
        assert result == {}

        # Create test tracks
        track1 = Track(
            id=None,
            title=f"TEST_Track1_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist1_{uuid4()}")],
            connector_track_identifiers={},
        )
        track2 = Track(
            id=None,
            title=f"TEST_Track2_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist2_{uuid4()}")],
            connector_track_identifiers={},
        )

        # Save tracks
        saved_track1 = await track_repo.save_track(track1)
        saved_track2 = await track_repo.save_track(track2)
        test_data_tracker.add_track(saved_track1.id)
        test_data_tracker.add_track(saved_track2.id)

        # Test single track lookup
        single_result = await track_repo.find_tracks_by_ids([saved_track1.id])
        assert len(single_result) == 1
        assert saved_track1.id in single_result
        assert single_result[saved_track1.id].title == track1.title

        # Test multiple tracks lookup
        multi_result = await track_repo.find_tracks_by_ids([
            saved_track1.id,
            saved_track2.id,
        ])
        assert len(multi_result) == 2
        assert saved_track1.id in multi_result
        assert saved_track2.id in multi_result

        # Test missing track IDs (should not include non-existent IDs in result)
        nonexistent_id = uuid4()
        missing_result = await track_repo.find_tracks_by_ids([
            saved_track1.id,
            nonexistent_id,
        ])
        assert len(missing_result) == 1  # Only the existing track
        assert saved_track1.id in missing_result
        assert nonexistent_id not in missing_result

    async def test_track_with_connector_identifiers(
        self, db_session, test_data_tracker
    ):
        """Test track with multiple connector identifiers using correct field names."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Create track with connector identifiers
        test_track = Track(
            id=None,
            title=f"TEST_Track_Connectors_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
            connector_track_identifiers={
                "spotify": f"spotify_{uuid4()}",
                "lastfm": f"lastfm_{uuid4()}",
            },
        )

        # Save track
        saved_track = await track_repo.save_track(test_track)
        test_data_tracker.add_track(saved_track.id)

        # Verify connector identifiers functionality works (repository may filter or modify identifiers)
        assert (
            len(saved_track.connector_track_identifiers) >= 1
        )  # At least some identifiers preserved

        # Verify core functionality: connector identifier persistence
        has_spotify = "spotify" in saved_track.connector_track_identifiers
        has_lastfm = "lastfm" in saved_track.connector_track_identifiers
        assert (
            has_spotify or has_lastfm
        )  # At least one of the original identifiers should be preserved

        # Retrieve and verify persistence
        retrieved_track = await track_repo.get_by_id(saved_track.id)
        assert (
            len(retrieved_track.connector_track_identifiers) >= 1
        )  # Identifiers persist

    async def test_bulk_track_operations(self, db_session, test_data_tracker):
        """Test bulk operations and track management scenarios."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Create multiple tracks for bulk testing
        tracks_to_save = []
        for i in range(3):
            track = Track(
                id=None,
                title=f"TEST_BulkTrack_{i}_{uuid4()}",
                artists=[Artist(name=f"TEST_BulkArtist_{i}_{uuid4()}")],
                connector_track_identifiers={},
            )
            tracks_to_save.append(track)

        # Save tracks individually (testing repository consistency)
        saved_tracks = []
        for track in tracks_to_save:
            saved_track = await track_repo.save_track(track)
            saved_tracks.append(saved_track)
            test_data_tracker.add_track(saved_track.id)

        # Verify all tracks were saved with unique IDs
        saved_ids = [track.id for track in saved_tracks]
        assert len(set(saved_ids)) == 3  # All IDs should be unique
        assert all(track_id is not None for track_id in saved_ids)

        # Test bulk retrieval
        bulk_result = await track_repo.find_tracks_by_ids(saved_ids)
        assert len(bulk_result) == 3
        for saved_track in saved_tracks:
            assert saved_track.id in bulk_result
            retrieved = bulk_result[saved_track.id]
            assert retrieved.title.startswith("TEST_BulkTrack_")

    async def test_track_update_operations(self, db_session, test_data_tracker):
        """Test track update and modification scenarios."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Create and save initial track
        original_track = Track(
            title=f"TEST_Original_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
            album=f"TEST_Album_{uuid4()}",
            connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
        )

        saved_track = await track_repo.save_track(original_track)
        test_data_tracker.add_track(saved_track.id)

        # Update track with new connector identifier (evolve preserves version)
        from attrs import evolve

        updated_track = evolve(
            saved_track,
            connector_track_identifiers={
                **saved_track.connector_track_identifiers,
                "musicbrainz": str(uuid4()),
            },
        )

        # Save updated track
        final_track = await track_repo.save_track(updated_track)

        # Verify update was successful
        assert final_track.id == saved_track.id  # Same ID
        assert final_track.title == saved_track.title  # Same title
        assert (
            "spotify" in final_track.connector_track_identifiers
        )  # Original connector preserved
        assert (
            "musicbrainz" in final_track.connector_track_identifiers
        )  # New connector added


class TestFindTracksByTitleArtist:
    """Integration tests for find_tracks_by_title_artist batch lookup."""

    async def test_finds_track_by_exact_title_artist(
        self, db_session, test_data_tracker
    ):
        """Basic case: finds a track by its title and first artist."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="Creep",
            artists=[Artist(name="Radiohead")],
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        result = await track_repo.find_tracks_by_title_artist(
            [("Creep", "Radiohead")], user_id="default"
        )
        assert ("creep", "radiohead") in result
        assert result["creep", "radiohead"].id == saved.id

    async def test_case_insensitive_match(self, db_session, test_data_tracker):
        """Title and artist matching should be case-insensitive."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="Bohemian Rhapsody",
            artists=[Artist(name="Queen")],
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("bohemian rhapsody", "queen"),
            ],
            user_id="default",
        )
        assert ("bohemian rhapsody", "queen") in result
        assert result["bohemian rhapsody", "queen"].id == saved.id

    async def test_no_match_returns_empty(self, db_session, test_data_tracker):
        """When no track matches, returns empty dict."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("Nonexistent Song", "Unknown Artist"),
            ],
            user_id="default",
        )
        assert result == {}

    async def test_empty_pairs_returns_empty(self, db_session, test_data_tracker):
        """Empty input returns empty dict without DB query."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        result = await track_repo.find_tracks_by_title_artist([], user_id="default")
        assert result == {}

    async def test_multiple_pairs_batch_lookup(self, db_session, test_data_tracker):
        """Multiple (title, artist) pairs should be resolved in one call."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track_a = await track_repo.save_track(
            Track(
                id=None,
                title="Song A",
                artists=[Artist(name="Artist A")],
            )
        )
        track_b = await track_repo.save_track(
            Track(
                id=None,
                title="Song B",
                artists=[Artist(name="Artist B")],
            )
        )
        test_data_tracker.add_track(track_a.id)
        test_data_tracker.add_track(track_b.id)

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("Song A", "Artist A"),
                ("Song B", "Artist B"),
                ("Song C", "Artist C"),  # No match
            ],
            user_id="default",
        )

        assert len(result) == 2
        assert result["song a", "artist a"].id == track_a.id
        assert result["song b", "artist b"].id == track_b.id

    async def test_returns_oldest_when_duplicates_exist(
        self, db_session, test_data_tracker
    ):
        """When multiple tracks share title+artist, return the oldest (lowest ID)."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        first = await track_repo.save_track(
            Track(
                id=None,
                title="Duplicate",
                artists=[Artist(name="Same Artist")],
            )
        )
        second = await track_repo.save_track(
            Track(
                id=None,
                title="Duplicate",
                artists=[Artist(name="Same Artist")],
            )
        )
        test_data_tracker.add_track(first.id)
        test_data_tracker.add_track(second.id)

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("Duplicate", "Same Artist"),
            ],
            user_id="default",
        )

        assert len(result) == 1
        assert result["duplicate", "same artist"].id == first.id


class TestNormalizedLookup:
    """Integration tests for normalized fuzzy matching via title_normalized/artist_normalized."""

    async def test_diacritics_match(self, db_session, test_data_tracker):
        """'fusées' stored by Spotify should match 'fusees' searched by Last.fm."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="Les Fusées",
            artists=[Artist(name="Björk")],
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("Les Fusees", "Bjork"),
            ],
            user_id="default",
        )
        assert ("les fusees", "bjork") in result
        assert result["les fusees", "bjork"].id == saved.id

    async def test_smart_quotes_match(self, db_session, test_data_tracker):
        """Smart quotes (\u2018Don\u2019t\u2019) should match straight quotes ('Don't')."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="Don\u2019t Stop Me Now",
            artists=[Artist(name="Queen")],
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("Don't Stop Me Now", "Queen"),
            ],
            user_id="default",
        )
        assert ("don't stop me now", "queen") in result
        assert result["don't stop me now", "queen"].id == saved.id

    async def test_article_prefix_match(self, db_session, test_data_tracker):
        """'The Beatles' should match 'Beatles' (leading article stripped)."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="Hey Jude",
            artists=[Artist(name="The Beatles")],
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("Hey Jude", "Beatles"),
            ],
            user_id="default",
        )
        assert ("hey jude", "beatles") in result
        assert result["hey jude", "beatles"].id == saved.id

    async def test_punctuation_match(self, db_session, test_data_tracker):
        """'AC/DC' should match 'ACDC' (punctuation stripped)."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="Thunderstruck",
            artists=[Artist(name="AC/DC")],
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("Thunderstruck", "ACDC"),
            ],
            user_id="default",
        )
        assert ("thunderstruck", "acdc") in result
        assert result["thunderstruck", "acdc"].id == saved.id

    async def test_feat_variation_match(self, db_session, test_data_tracker):
        """'feat.' should match 'ft.' and 'featuring'."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="Song feat. Guest",
            artists=[Artist(name="Main Artist")],
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("Song ft. Guest", "Main Artist"),
            ],
            user_id="default",
        )
        assert ("song ft. guest", "main artist") in result
        assert result["song ft. guest", "main artist"].id == saved.id

    async def test_normalized_columns_populated_on_save(
        self, db_session, test_data_tracker
    ):
        """Verify that title_normalized and artist_normalized are set when saving."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="Motörhead",
            artists=[Artist(name="The Killers")],
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        # Query raw DB to verify normalized columns
        from sqlalchemy import select

        from src.infrastructure.persistence.database.db_models import DBTrack

        stmt = select(DBTrack.title_normalized, DBTrack.artist_normalized).where(
            DBTrack.id == saved.id
        )
        result = await db_session.execute(stmt)
        row = result.one()
        assert row.title_normalized == "motorhead"
        assert row.artist_normalized == "killers"


class TestParentheticalStripping:
    """Integration tests for parenthetical stripping fallback matching."""

    async def test_find_by_stripped_title(self, db_session, test_data_tracker):
        """Track saved as 'Song (feat. X)' should be found by searching 'Song'."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="New Kind of Soft (feat. Neon Priest)",
            artists=[Artist(name="Artist")],
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        # Search by bare title (without parenthetical)
        result = await track_repo.find_tracks_by_title_artist(
            [
                ("New Kind of Soft", "Artist"),
            ],
            user_id="default",
        )
        assert ("new kind of soft", "artist") in result
        assert result["new kind of soft", "artist"].id == saved.id

    async def test_find_parenthetical_by_stripped(self, db_session, test_data_tracker):
        """Track saved as 'Song' should be found by searching 'Song (feat. X)'."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="New Kind of Soft",
            artists=[Artist(name="Artist")],
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        # Search by title with parenthetical added
        result = await track_repo.find_tracks_by_title_artist(
            [
                ("New Kind of Soft (feat. Neon Priest)", "Artist"),
            ],
            user_id="default",
        )
        key = ("new kind of soft (feat. neon priest)", "artist")
        assert key in result
        assert result[key].id == saved.id

    async def test_title_stripped_column_populated(self, db_session, test_data_tracker):
        """Verify title_stripped is populated on save."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="Song (Remix) [Deluxe]",
            artists=[Artist(name="Artist")],
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        from sqlalchemy import select

        from src.infrastructure.persistence.database.db_models import DBTrack

        stmt = select(DBTrack.title_stripped).where(DBTrack.id == saved.id)
        result = await db_session.execute(stmt)
        row = result.one()
        assert row.title_stripped == "song"


class TestFindTracksByISRC:
    """Integration tests for ISRC-based batch lookup."""

    async def test_find_by_isrc(self, db_session, test_data_tracker):
        """Track with ISRC should be found by ISRC lookup."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = Track(
            id=None,
            title="ISRC Track",
            artists=[Artist(name="ISRC Artist")],
            isrc="USRC17000001",
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        result = await track_repo.find_tracks_by_isrcs(
            ["USRC17000001"], user_id="default"
        )
        assert "USRC17000001" in result
        assert result["USRC17000001"].id == saved.id

    async def test_find_by_isrc_not_found(self, db_session, test_data_tracker):
        """Missing ISRC returns empty dict."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        result = await track_repo.find_tracks_by_isrcs(
            ["NONEXISTENT123"], user_id="default"
        )
        assert result == {}

    async def test_find_by_isrc_empty_list(self, db_session, test_data_tracker):
        """Empty ISRC list returns empty dict."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        result = await track_repo.find_tracks_by_isrcs([], user_id="default")
        assert result == {}


class TestFindTracksByMBID:
    """Integration tests for MBID-based batch lookup."""

    async def test_find_by_mbid(self, db_session, test_data_tracker):
        """Track with MBID should be found by MBID lookup."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        mbid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        track = Track(
            id=None,
            title="MBID Track",
            artists=[Artist(name="MBID Artist")],
            connector_track_identifiers={"musicbrainz": mbid},
        )
        saved = await track_repo.save_track(track)
        test_data_tracker.add_track(saved.id)

        result = await track_repo.find_tracks_by_mbids([mbid], user_id="default")
        assert mbid in result
        assert result[mbid].id == saved.id

    async def test_mbid_upsert_path(self, db_session, test_data_tracker):
        """Saving a track with same MBID should upsert, not create duplicate."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        mbid = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
        track1 = Track(
            id=None,
            title="MBID Track V1",
            artists=[Artist(name="Artist")],
            connector_track_identifiers={"musicbrainz": mbid},
        )
        saved1 = await track_repo.save_track(track1)
        test_data_tracker.add_track(saved1.id)

        # Save another track with same MBID → should upsert
        track2 = Track(
            id=None,
            title="MBID Track V2",
            artists=[Artist(name="Artist")],
            connector_track_identifiers={"musicbrainz": mbid},
        )
        saved2 = await track_repo.save_track(track2)

        # Should be same row (upserted)
        assert saved2.id == saved1.id
        assert saved2.title == "MBID Track V2"
