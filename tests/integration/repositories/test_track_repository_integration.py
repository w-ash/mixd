"""Integration tests for TrackRepository with real database operations following modern patterns."""

from datetime import UTC, datetime
from uuid import uuid4

from attrs import evolve
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql

from src.domain.entities import ArtistCredit
from src.domain.exceptions import OptimisticLockError
from src.domain.matching import normalize_for_comparison, strip_parentheticals
from src.domain.repositories.errors import IdentityKeyClaimedError
from src.infrastructure.persistence.database.models import DBTrack
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from src.infrastructure.persistence.repositories.track.core import (
    build_title_artist_probe,
)
from tests.fixtures import make_track


class TestTrackRepositoryIntegration:
    """Integration tests for track repository with real database operations."""

    async def test_save_and_retrieve_track(self, db_session):
        """Test saving and retrieving a track with automatic cleanup tracking."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        test_track = make_track(
            title=f"TEST_Track_{uuid4()}",
            artist=f"TEST_Artist_{uuid4()}",
            album=f"TEST_Album_{uuid4()}",
            duration_ms=180000,
            connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
        )

        saved_track = await track_repo.save_track(test_track)

        assert saved_track.id is not None
        assert saved_track.title == test_track.title
        assert (
            saved_track.artists[0].credited_name == test_track.artists[0].credited_name
        )
        assert saved_track.album == test_track.album
        assert saved_track.duration_ms == test_track.duration_ms

        retrieved_track = await track_repo.get_by_id(saved_track.id)
        assert retrieved_track is not None
        assert retrieved_track.title == test_track.title
        assert len(retrieved_track.artists) == 1
        assert (
            retrieved_track.artists[0].credited_name
            == test_track.artists[0].credited_name
        )

    async def test_find_tracks_by_ids_operations(self, db_session):
        """Test find_tracks_by_ids with empty list, single track, multiple tracks, and missing IDs."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        result = await track_repo.find_tracks_by_ids([])
        assert result == {}

        track1 = make_track(
            title=f"TEST_Track1_{uuid4()}",
            artist=f"TEST_Artist1_{uuid4()}",
            connector_track_identifiers={},
        )
        track2 = make_track(
            title=f"TEST_Track2_{uuid4()}",
            artist=f"TEST_Artist2_{uuid4()}",
            connector_track_identifiers={},
        )

        saved_track1 = await track_repo.save_track(track1)
        saved_track2 = await track_repo.save_track(track2)

        single_result = await track_repo.find_tracks_by_ids([saved_track1.id])
        assert len(single_result) == 1
        assert saved_track1.id in single_result
        assert single_result[saved_track1.id].title == track1.title

        multi_result = await track_repo.find_tracks_by_ids([
            saved_track1.id,
            saved_track2.id,
        ])
        assert len(multi_result) == 2
        assert saved_track1.id in multi_result
        assert saved_track2.id in multi_result

        nonexistent_id = uuid4()
        missing_result = await track_repo.find_tracks_by_ids([
            saved_track1.id,
            nonexistent_id,
        ])
        assert len(missing_result) == 1  # Only the existing track
        assert saved_track1.id in missing_result
        assert nonexistent_id not in missing_result

    async def test_track_with_connector_identifiers(self, db_session):
        """Test track with multiple connector identifiers using correct field names."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        test_track = make_track(
            title=f"TEST_Track_Connectors_{uuid4()}",
            artist=f"TEST_Artist_{uuid4()}",
            connector_track_identifiers={
                "spotify": f"spotify_{uuid4()}",
                "lastfm": f"lastfm_{uuid4()}",
            },
        )

        saved_track = await track_repo.save_track(test_track)

        # A canonical's connector ids come from its mappings alone, so
        # save_track persists none of them — every connector needs its own
        # map_track_to_connector call.
        assert "spotify" not in saved_track.connector_track_identifiers
        assert "lastfm" not in saved_track.connector_track_identifiers

        retrieved_track = await track_repo.get_by_id(saved_track.id)
        assert "spotify" not in retrieved_track.connector_track_identifiers
        assert "lastfm" not in retrieved_track.connector_track_identifiers

    async def test_an_update_writes_every_changed_column(self, db_session):
        """Saving an edited track writes its columns, not only a new version.

        The read-back is a column select, so the values come from the table
        and not from an ORM instance the session already holds.
        """
        track_repo = get_unit_of_work(db_session).get_track_repository()
        saved = await track_repo.save_track(
            make_track(
                title="Before",
                artist="Old Artist",
                album="Old Album",
                duration_ms=200_000,
                release_date=datetime(2001, 1, 1, tzinfo=UTC),
                isrc="QZTST2600001",
            )
        )

        _ = await track_repo.save_track(
            evolve(
                saved,
                title="After (Live)",
                artists=[
                    ArtistCredit(credited_name="New Artist"),
                    ArtistCredit(credited_name="Guest"),
                ],
                album="New Album",
                duration_ms=245_733,
                release_date=datetime(2024, 5, 17, tzinfo=UTC),
                isrc="QZTST2600002",
            )
        )

        row = (
            await db_session.execute(
                select(
                    DBTrack.title,
                    DBTrack.artists,
                    DBTrack.album,
                    DBTrack.duration_ms,
                    DBTrack.release_date,
                    DBTrack.isrc,
                    DBTrack.title_normalized,
                    DBTrack.artist_normalized,
                    DBTrack.title_stripped,
                    DBTrack.artists_text,
                    DBTrack.version,
                ).where(DBTrack.id == saved.id)
            )
        ).one()
        assert row.title == "After (Live)"
        assert row.artists == {"names": ["New Artist", "Guest"]}
        assert row.album == "New Album"
        assert row.duration_ms == 245_733
        assert row.release_date == datetime(2024, 5, 17, tzinfo=UTC)
        assert row.isrc == "QZTST2600002"
        assert row.title_normalized == "after live"
        assert row.artist_normalized == "new artist"
        assert row.title_stripped == "after"
        assert row.artists_text == "New Artist, Guest"
        assert row.version == 2


class TestTrackOptimisticLocking:
    """Integration tests for optimistic concurrency control on tracks."""

    async def test_save_track_increments_version(self, db_session):
        """Saving a persisted track should increment its version."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = make_track(
            title=f"TEST_Version_{uuid4()}",
            artist="Test Artist",
        )

        saved = await track_repo.save_track(track)
        assert saved.version == 1

        # Modify and save again — version should increment to 2
        modified = evolve(saved, title=f"TEST_Version_Updated_{uuid4()}")
        updated = await track_repo.save_track(modified)
        assert updated.version == 2
        assert updated.id == saved.id

        # Reload from DB to confirm persistence
        reloaded = await track_repo.get_by_id(saved.id)
        assert reloaded.version == 2

    async def test_save_track_rejects_stale_version(self, db_session):
        """Saving a track with a stale version should raise OptimisticLockError."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = make_track(
            title=f"TEST_Stale_{uuid4()}",
            artist="Test Artist",
        )

        saved = await track_repo.save_track(track)

        # Simulate two concurrent loads — both hold version=1
        stale_copy = evolve(saved, title="Stale Edit")

        fresh_edit = evolve(saved, title="Fresh Edit")
        await track_repo.save_track(fresh_edit)

        # Second save with stale version=1 should fail
        with pytest.raises(OptimisticLockError) as exc_info:
            await track_repo.save_track(stale_copy)

        assert exc_info.value.expected_version == 1


class TestFillBlankMetadata:
    """One statement fills only the columns a row holds NULL in, under the version."""

    async def test_only_blank_columns_take_the_fill_and_the_version_bumps(
        self, db_session
    ):
        track_repo = get_unit_of_work(db_session).get_track_repository()
        sparse, kept = await track_repo.save_tracks([
            make_track(title=f"TEST_Fill_{uuid4()}", duration_ms=None, album=None),
            make_track(title=f"TEST_Kept_{uuid4()}", duration_ms=100_000, album="Held"),
        ])

        filled = await track_repo.fill_blank_metadata([
            evolve(sparse, duration_ms=245_733, album="Mixed"),
            evolve(kept, duration_ms=245_733, album="Mixed"),
        ])

        assert [t.version for t in filled] == [2, 2]
        assert (filled[0].duration_ms, filled[0].album) == (245_733, "Mixed")
        rows = {
            row.id: row
            for row in (
                await db_session.execute(
                    select(
                        DBTrack.id, DBTrack.duration_ms, DBTrack.album, DBTrack.version
                    ).where(DBTrack.id.in_([sparse.id, kept.id]))
                )
            ).all()
        }
        assert (rows[sparse.id].duration_ms, rows[sparse.id].album) == (
            245_733,
            "Mixed",
        )
        assert (rows[kept.id].duration_ms, rows[kept.id].album) == (100_000, "Held")
        assert {row.version for row in rows.values()} == {2}

    async def test_a_stale_version_writes_nothing_and_raises(self, db_session):
        track_repo = get_unit_of_work(db_session).get_track_repository()
        saved = await track_repo.save_track(
            make_track(title=f"TEST_Stale_Fill_{uuid4()}", duration_ms=None)
        )
        _ = await track_repo.save_track(evolve(saved, title="Moved on"))

        with pytest.raises(OptimisticLockError) as exc_info:
            _ = await track_repo.fill_blank_metadata([
                evolve(saved, duration_ms=245_733)
            ])

        assert exc_info.value.expected_version == 1
        reloaded = await track_repo.get_by_id(saved.id)
        assert reloaded.duration_ms is None
        assert reloaded.version == 2


class TestFindTracksByTitleArtist:
    """Integration tests for find_tracks_by_title_artist batch lookup."""

    async def test_finds_track_by_exact_title_artist(self, db_session):
        """Basic case: finds a track by its title and first artist."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = make_track(
            title="Creep",
            artist="Radiohead",
        )
        saved = await track_repo.save_track(track)

        result = await track_repo.find_tracks_by_title_artist(
            [("Creep", "Radiohead")], user_id="default"
        )
        assert ("creep", "radiohead") in result
        assert result["creep", "radiohead"].id == saved.id

    async def test_no_match_returns_empty(self, db_session):
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

    async def test_empty_pairs_returns_empty(self, db_session):
        """Empty input returns empty dict without DB query."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        result = await track_repo.find_tracks_by_title_artist([], user_id="default")
        assert result == {}

    async def test_multiple_pairs_batch_lookup(self, db_session):
        """Multiple (title, artist) pairs should be resolved in one call."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track_a = await track_repo.save_track(
            make_track(
                title="Song A",
                artist="Artist A",
            )
        )
        track_b = await track_repo.save_track(
            make_track(
                title="Song B",
                artist="Artist B",
            )
        )

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

    async def test_returns_oldest_when_duplicates_exist(self, db_session):
        """When multiple tracks share title+artist, return the oldest (lowest ID)."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        first = await track_repo.save_track(
            make_track(
                title="Duplicate",
                artist="Same Artist",
            )
        )
        second = await track_repo.save_track(
            make_track(
                title="Duplicate",
                artist="Same Artist",
            )
        )

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("Duplicate", "Same Artist"),
            ],
            user_id="default",
        )

        assert len(result) == 1
        assert result["duplicate", "same artist"].id == first.id

    async def test_parenthetical_variants_match_both_directions(self, db_session):
        """The stored and queried titles may each carry the parenthetical."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        with_paren = await track_repo.save_track(
            make_track(title="Sunrise (feat. Nova)", artist="Aster")
        )
        without_paren = await track_repo.save_track(
            make_track(title="Moonset", artist="Borea")
        )

        result = await track_repo.find_tracks_by_title_artist(
            [("Sunrise", "Aster"), ("Moonset (feat. Nova)", "Borea")],
            user_id="default",
        )

        assert result["sunrise", "aster"].id == with_paren.id
        assert result["moonset (feat. nova)", "borea"].id == without_paren.id

    async def test_a_track_matching_two_probe_rows_appears_once(self, db_session):
        """A title whose variants both match must not resolve twice."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        saved = await track_repo.save_track(
            make_track(title="Echo (Reprise)", artist="Cirrus")
        )

        result = await track_repo.find_tracks_by_title_artist(
            [("Echo (Reprise)", "Cirrus"), ("Echo", "Cirrus")],
            user_id="default",
        )

        # First pair wins; the second finds nothing left to claim.
        assert result["echo (reprise)", "cirrus"].id == saved.id
        assert ("echo", "cirrus") not in result

    async def test_two_hundred_pairs_resolve_in_one_query(self, db_session):
        """The array form takes two binds at any N — no parameter ceiling."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        saved = await track_repo.save_track(
            make_track(title="Needle", artist="Haystack")
        )
        pairs = [(f"Filler {n}", f"Artist {n}") for n in range(199)]
        pairs.append(("Needle", "Haystack"))

        result = await track_repo.find_tracks_by_title_artist(pairs, user_id="default")

        assert result["needle", "haystack"].id == saved.id

    @pytest.mark.slow
    async def test_probe_uses_the_user_scoped_index_at_scale(self, db_session):
        """The plan must not degrade into a seq scan as the library grows.

        This is the growth term the v0.10.2.11 audit measured as a declining
        within-run resolution rate; a seq scan here is that regression.
        """
        await db_session.execute(
            text("""
                INSERT INTO tracks (
                    id, user_id, version, title, artists, title_normalized,
                    artist_normalized, title_stripped, created_at, updated_at
                )
                SELECT
                    gen_random_uuid(), 'default', 1,
                    'Track ' || n, '[]'::jsonb,
                    'track' || n, 'artist' || n, 'track' || n, now(), now()
                FROM generate_series(1, 3000) AS n
            """)
        )
        await db_session.execute(text("ANALYZE tracks"))

        stmt = build_title_artist_probe([("track42", "artist42")], user_id="default")
        compiled = stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
        rows = await db_session.execute(text(f"EXPLAIN {compiled}"))
        plan = "\n".join(str(row[0]) for row in rows.all())

        assert "Seq Scan on tracks" not in plan
        assert "ix_tracks_user_normalized_lookup" in plan


class TestNormalizedLookup:
    """Integration tests for normalized fuzzy matching via title_normalized/artist_normalized."""

    async def test_diacritics_match(self, db_session):
        """'fusées' stored by Spotify should match 'fusees' searched by Last.fm."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = make_track(
            title="Les Fusées",
            artist="Björk",
        )
        saved = await track_repo.save_track(track)

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("Les Fusees", "Bjork"),
            ],
            user_id="default",
        )
        assert ("les fusees", "bjork") in result
        assert result["les fusees", "bjork"].id == saved.id

    async def test_smart_quotes_match(self, db_session):
        """Smart quotes (\u2018Don\u2019t\u2019) should match straight quotes ('Don't')."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = make_track(
            title="Don\u2019t Stop Me Now",
            artist="Queen",
        )
        saved = await track_repo.save_track(track)

        result = await track_repo.find_tracks_by_title_artist(
            [
                ("Don't Stop Me Now", "Queen"),
            ],
            user_id="default",
        )
        assert ("don't stop me now", "queen") in result
        assert result["don't stop me now", "queen"].id == saved.id

    async def test_probe_artist_is_normalized_like_the_stored_one(self, db_session):
        """A probe that repeats the stored artist verbatim still matches.

        The stored column holds "beatles" (article and case removed), so the
        probe side must normalize the artist the same way, not only lowercase it.
        """
        track_repo = get_unit_of_work(db_session).get_track_repository()
        saved = await track_repo.save_track(
            make_track(title="Hey Jude", artist="The Beatles")
        )

        result = await track_repo.find_tracks_by_title_artist(
            [("Hey Jude", "The Beatles")], user_id="default"
        )

        assert result["hey jude", "the beatles"].id == saved.id

    async def test_normalized_columns_populated_on_save(self, db_session):
        """Verify that title_normalized and artist_normalized are set when saving."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = make_track(
            title="Motörhead",
            artist="The Killers",
        )
        saved = await track_repo.save_track(track)

        # Query raw DB to verify normalized columns

        stmt = select(DBTrack.title_normalized, DBTrack.artist_normalized).where(
            DBTrack.id == saved.id
        )
        result = await db_session.execute(stmt)
        row = result.one()
        assert row.title_normalized == "motorhead"
        assert row.artist_normalized == "killers"


class TestParentheticalStripping:
    """Integration tests for parenthetical stripping fallback matching."""

    async def test_title_stripped_column_populated(self, db_session):
        """Verify title_stripped is populated on save."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = make_track(
            title="Song (Remix) [Deluxe]",
            artist="Artist",
        )
        saved = await track_repo.save_track(track)

        stmt = select(DBTrack.title_stripped).where(DBTrack.id == saved.id)
        result = await db_session.execute(stmt)
        row = result.one()
        assert row.title_stripped == "song"


class TestFindTracksByISRC:
    """Integration tests for ISRC-based batch lookup."""

    async def test_find_by_isrc(self, db_session):
        """Track with ISRC should be found by ISRC lookup."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        track = make_track(
            title="ISRC Track",
            artist="ISRC Artist",
            isrc="USRC17000001",
        )
        saved = await track_repo.save_track(track)

        result = await track_repo.find_tracks_by_isrcs(
            ["USRC17000001"], user_id="default"
        )
        assert "USRC17000001" in result
        assert result["USRC17000001"].id == saved.id

    async def test_find_by_isrc_not_found(self, db_session):
        """Missing ISRC returns empty dict."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        result = await track_repo.find_tracks_by_isrcs(
            ["NONEXISTENT123"], user_id="default"
        )
        assert result == {}

    async def test_find_by_isrc_empty_list(self, db_session):
        """Empty ISRC list returns empty dict."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        result = await track_repo.find_tracks_by_isrcs([], user_id="default")
        assert result == {}


class TestTrackNormalizedColumns:
    """Verify save_track populates the four columns that back fuzzy search.

    Library search (pg_trgm indexes on title_normalized, artist_normalized,
    title_stripped) only finds rows where these columns are populated. Any
    persistence path that bypasses normalization makes the row invisible to
    search.
    """

    @pytest.mark.parametrize(
        ("title", "artist"),
        [
            ("夜に駆ける", "YOASOBI"),  # Japanese (kanji + hiragana)
            ("爱情转移", "陈奕迅"),  # Simplified Chinese
            ("愛情轉移", "陳奕迅"),  # Traditional Chinese
            ("Раммштайн", "Раммштайн"),  # Cyrillic
            ("Ωραίο", "Δήμητρα"),  # Greek with diacritics
            ("The Beatles", "The Beatles"),  # ASCII baseline
        ],
    )
    async def test_save_track_populates_normalized_columns(
        self, db_session, title: str, artist: str
    ):
        track = make_track(version=0, title=title, artist=artist)
        uow = get_unit_of_work(db_session)
        saved = await uow.get_track_repository().save_track(track)

        # Re-read from DB rather than trusting the in-memory return.
        await db_session.flush()
        db_row = await db_session.get(DBTrack, saved.id)
        assert db_row is not None

        assert db_row.title_normalized == normalize_for_comparison(title)
        assert db_row.artist_normalized == normalize_for_comparison(artist)
        assert db_row.title_stripped == normalize_for_comparison(
            strip_parentheticals(title)
        )
        assert db_row.artists_text == artist


class TestSaveTracksBulk:
    """``save_tracks`` is the batch form of ``save_track``'s insert arm."""

    async def test_a_batch_of_new_canonicals_is_inserted_in_input_order(
        self, db_session
    ):
        track_repo = get_unit_of_work(db_session).get_track_repository()
        tracks = [
            make_track(
                title=f"TEST_Bulk_{n}_{uuid4()}",
                artist=f"TEST_Artist_{uuid4()}",
                duration_ms=180_000 + n,
                connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
            )
            for n in range(3)
        ]

        saved = await track_repo.save_tracks(tracks)

        assert [t.title for t in saved] == [t.title for t in tracks]
        assert len({t.id for t in saved}) == 3
        for track in saved:
            assert track.id is not None
            retrieved = await track_repo.get_by_id(track.id)
            assert retrieved.title == track.title

    async def test_two_canonicals_may_carry_one_spotify_id(self, db_session):
        """A Spotify id is not an identity key: it lives in the mappings, so
        two canonicals naming the same one are both inserted. Which canonical
        an id belongs to is the resolution planner's call, not the table's."""
        track_repo = get_unit_of_work(db_session).get_track_repository()
        spotify_id = f"spotify_{uuid4()}"

        saved = await track_repo.save_tracks([
            make_track(
                title=f"TEST_First_{uuid4()}",
                connector_track_identifiers={"spotify": spotify_id},
            ),
            make_track(
                title=f"TEST_Second_{uuid4()}",
                connector_track_identifiers={"spotify": spotify_id},
            ),
        ])

        assert len({track.id for track in saved}) == 2

    async def test_a_claimed_isrc_is_refused_naming_the_key(self, db_session):
        """A key the table already holds is not new, and the batch does not
        decide what it is instead: it raises, naming the claimed key, and
        writes nothing — the fresh row beside it included."""
        track_repo = get_unit_of_work(db_session).get_track_repository()
        isrc = f"TEST{uuid4().hex[:8].upper()}"
        title = f"TEST_Owner_{uuid4()}"
        owner = await track_repo.save_track(
            make_track(title=title, isrc=isrc, duration_ms=200_000)
        )
        fresh_title = f"TEST_Fresh_{uuid4()}"

        with pytest.raises(IdentityKeyClaimedError) as raised:
            _ = await track_repo.save_tracks([
                make_track(
                    title=f"TEST_Incoming_{uuid4()}", isrc=isrc, duration_ms=200_500
                ),
                make_track(
                    title=fresh_title,
                    connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
                ),
            ])

        assert raised.value.keys == {("isrc", owner.user_id, isrc)}
        fresh_rows = (
            await db_session.execute(
                select(func.count())
                .select_from(DBTrack)
                .where(DBTrack.title == fresh_title)
            )
        ).scalar_one()
        assert fresh_rows == 0

    async def test_two_rows_claiming_one_key_in_one_batch_are_refused(self, db_session):
        """An in-batch twin is the planner's to fold onto a leader before the
        batch is handed here; two rows naming one key would race the unique
        constraint, so the second is refused up front."""
        track_repo = get_unit_of_work(db_session).get_track_repository()
        isrc = f"TEST{uuid4().hex[:8].upper()}"

        with pytest.raises(IdentityKeyClaimedError) as raised:
            _ = await track_repo.save_tracks([
                make_track(title=f"TEST_First_{uuid4()}", isrc=isrc),
                make_track(title=f"TEST_Second_{uuid4()}", isrc=isrc),
            ])

        assert {value for _, _, value in raised.value.keys} == {isrc}

    async def test_an_empty_batch_touches_nothing(self, db_session):
        track_repo = get_unit_of_work(db_session).get_track_repository()

        assert await track_repo.save_tracks([]) == []

    async def test_a_version_bump_row_takes_the_update_arm(self, db_session):
        """The optimistic-locking arm (version > 0) has no batch form and is
        neither an insert nor a claimed-key refusal."""
        track_repo = get_unit_of_work(db_session).get_track_repository()
        saved = await track_repo.save_track(
            make_track(
                title=f"TEST_Versioned_{uuid4()}",
                connector_track_identifiers={"spotify": f"TEST_spotify_{uuid4()}"},
            )
        )

        (updated,) = await track_repo.save_tracks([
            evolve(saved, album=f"TEST_Album_{uuid4()}")
        ])

        assert updated.version == saved.version + 1
