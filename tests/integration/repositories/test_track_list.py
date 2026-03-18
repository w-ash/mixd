"""Integration tests for TrackRepository.list_tracks() — library listing.

Verifies search, filtering, sorting, and pagination with a real PostgreSQL database.
Each test gets a fresh DB via the db_session fixture.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.db_models import (
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


async def _insert_track(
    session: AsyncSession,
    title: str,
    artist: str = "Test Artist",
    album: str | None = None,
    duration_ms: int | None = None,
) -> int:
    """Insert a track directly into the DB and return its ID."""
    db_track = DBTrack(
        title=title,
        artists={"names": [artist]},
        artists_text=artist,
        album=album,
        duration_ms=duration_ms,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(db_track)
    await session.flush()
    await session.refresh(db_track)
    return db_track.id


async def _like_track(
    session: AsyncSession, track_id: int, service: str = "spotify"
) -> None:
    """Create a liked status for a track."""
    like = DBTrackLike(
        track_id=track_id,
        service=service,
        is_liked=True,
        liked_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(like)
    await session.flush()


class TestListTracksBasic:
    """Basic listing and pagination."""

    async def test_empty_database_returns_empty(self, db_session: AsyncSession) -> None:
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks()

        assert tracks == []
        assert total == 0

    async def test_returns_tracks_with_pagination_metadata(
        self, db_session: AsyncSession
    ) -> None:
        await _insert_track(db_session, "Track A")
        await _insert_track(db_session, "Track B")
        await _insert_track(db_session, "Track C")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks(limit=2, offset=0)

        assert total == 3
        assert len(tracks) == 2

    async def test_pagination_offset(self, db_session: AsyncSession) -> None:
        await _insert_track(db_session, "Alpha")
        await _insert_track(db_session, "Beta")
        await _insert_track(db_session, "Gamma")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks(
            sort_by="title_asc", limit=2, offset=1
        )

        assert total == 3
        assert len(tracks) == 2
        assert tracks[0].title == "Beta"
        assert tracks[1].title == "Gamma"


class TestListTracksSearch:
    """Text search across title, artist, album."""

    async def test_search_by_title(self, db_session: AsyncSession) -> None:
        await _insert_track(db_session, "Creep", artist="Radiohead")
        await _insert_track(db_session, "Karma Police", artist="Radiohead")
        await _insert_track(db_session, "Yellow", artist="Coldplay")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks(query="Creep")

        assert total == 1
        assert tracks[0].title == "Creep"

    async def test_search_by_artist_json(self, db_session: AsyncSession) -> None:
        await _insert_track(db_session, "Creep", artist="Radiohead")
        await _insert_track(db_session, "Yellow", artist="Coldplay")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks(query="Radiohead")

        assert total == 1
        assert tracks[0].title == "Creep"

    async def test_search_by_album(self, db_session: AsyncSession) -> None:
        await _insert_track(db_session, "Creep", album="Pablo Honey")
        await _insert_track(db_session, "Yellow", album="Parachutes")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks(query="Pablo")

        assert total == 1
        assert tracks[0].title == "Creep"

    async def test_search_case_insensitive(self, db_session: AsyncSession) -> None:
        await _insert_track(db_session, "Creep", artist="Radiohead")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks(query="radiohead")

        assert total == 1


class TestListTracksFilters:
    """Liked and connector filters."""

    async def test_filter_liked_true(self, db_session: AsyncSession) -> None:
        id1 = await _insert_track(db_session, "Liked Song")
        await _insert_track(db_session, "Not Liked")
        await _like_track(db_session, id1)

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks(liked=True)

        assert total == 1
        assert tracks[0].title == "Liked Song"

    async def test_filter_liked_false(self, db_session: AsyncSession) -> None:
        id1 = await _insert_track(db_session, "Liked Song")
        await _insert_track(db_session, "Not Liked")
        await _like_track(db_session, id1)

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks(liked=False)

        assert total == 1
        assert tracks[0].title == "Not Liked"

    async def test_filter_by_connector(self, db_session: AsyncSession) -> None:
        from src.infrastructure.persistence.database.db_models import (
            DBConnectorTrack,
        )

        id1 = await _insert_track(db_session, "Spotify Track")
        await _insert_track(db_session, "No Connector")

        # Create connector track and mapping
        ct = DBConnectorTrack(
            connector_name="spotify",
            connector_track_identifier=f"sp_{uuid4().hex[:8]}",
            title="Spotify Track",
            artists={"names": ["Artist"]},
            raw_metadata={},
            last_updated=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session = db_session
        session.add(ct)
        await session.flush()
        await session.refresh(ct)

        mapping = DBTrackMapping(
            track_id=id1,
            connector_track_id=ct.id,
            connector_name="spotify",
            match_method="direct",
            confidence=100,
            is_primary=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(mapping)
        await session.flush()

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks(connector="spotify")

        assert total == 1
        assert tracks[0].title == "Spotify Track"


class TestListTracksSorting:
    """Sort order verification."""

    async def test_sort_title_asc(self, db_session: AsyncSession) -> None:
        await _insert_track(db_session, "Zebra")
        await _insert_track(db_session, "Alpha")
        await _insert_track(db_session, "Mango")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, *_ = await track_repo.list_tracks(sort_by="title_asc")

        assert [t.title for t in tracks] == ["Alpha", "Mango", "Zebra"]

    async def test_sort_title_desc(self, db_session: AsyncSession) -> None:
        await _insert_track(db_session, "Zebra")
        await _insert_track(db_session, "Alpha")
        await _insert_track(db_session, "Mango")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, *_ = await track_repo.list_tracks(sort_by="title_desc")

        assert [t.title for t in tracks] == ["Zebra", "Mango", "Alpha"]

    async def test_sort_duration(self, db_session: AsyncSession) -> None:
        await _insert_track(db_session, "Long", duration_ms=300000)
        await _insert_track(db_session, "Short", duration_ms=120000)
        await _insert_track(db_session, "Medium", duration_ms=200000)

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, *_ = await track_repo.list_tracks(sort_by="duration_asc")

        assert [t.title for t in tracks] == ["Short", "Medium", "Long"]


class TestListTracksPaginationBoundary:
    """Pagination edge cases."""

    async def test_offset_beyond_total_returns_empty(
        self, db_session: AsyncSession
    ) -> None:
        """Requesting offset > total should return empty list, not error."""
        await _insert_track(db_session, "Only Track")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks(offset=100, limit=50)

        assert total == 1
        assert tracks == []


class TestListTracksLikedIds:
    """Verify liked_track_ids (third element) from list_tracks."""

    async def test_liked_ids_returned_for_liked_tracks(
        self, db_session: AsyncSession
    ) -> None:
        """liked_track_ids should contain IDs of tracks with TrackLike records."""
        id1 = await _insert_track(db_session, "Liked Song")
        id2 = await _insert_track(db_session, "Also Liked")
        await _insert_track(db_session, "Not Liked")
        await _like_track(db_session, id1)
        await _like_track(db_session, id2)

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        _, _, liked_ids, _ = await track_repo.list_tracks()

        assert id1 in liked_ids
        assert id2 in liked_ids
        assert len(liked_ids) == 2

    async def test_empty_liked_ids_when_no_likes(
        self, db_session: AsyncSession
    ) -> None:
        await _insert_track(db_session, "Unloved Track")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        _, _, liked_ids, _ = await track_repo.list_tracks()

        assert liked_ids == set()


class TestListTracksCombinedFilters:
    """Test multiple filters applied simultaneously."""

    async def test_query_and_liked_combined(self, db_session: AsyncSession) -> None:
        """Search query + liked filter should intersect, not union."""
        id1 = await _insert_track(db_session, "Creep", artist="Radiohead")
        id2 = await _insert_track(db_session, "Karma Police", artist="Radiohead")
        await _insert_track(db_session, "Yellow", artist="Coldplay")
        await _like_track(db_session, id1)  # Creep is liked
        # Karma Police is NOT liked

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        tracks, total, _, _ = await track_repo.list_tracks(query="Radiohead", liked=True)

        assert total == 1
        assert tracks[0].title == "Creep"


class TestListTracksKeysetPagination:
    """Keyset (cursor) pagination — O(1) seeks via WHERE (sort_col, id) > (v, id)."""

    async def test_keyset_title_asc_matches_offset(
        self, db_session: AsyncSession
    ) -> None:
        """Keyset forward navigation produces the same results as offset."""
        await _insert_track(db_session, "Alpha")
        await _insert_track(db_session, "Beta")
        await _insert_track(db_session, "Gamma")
        await _insert_track(db_session, "Delta")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Page 1 via offset
        page1_offset, _, _, _ = await track_repo.list_tracks(
            sort_by="title_asc", limit=2, offset=0
        )
        # Page 1 also via keyset (no cursor = first page)
        page1_keyset, _, _, next_key = await track_repo.list_tracks(
            sort_by="title_asc", limit=2
        )
        assert [t.title for t in page1_offset] == [t.title for t in page1_keyset]
        assert next_key is not None

        # Page 2 via offset
        page2_offset, _, _, _ = await track_repo.list_tracks(
            sort_by="title_asc", limit=2, offset=2
        )
        # Page 2 via keyset
        sort_val, last_id = next_key
        page2_keyset, _, _, _ = await track_repo.list_tracks(
            sort_by="title_asc", limit=2, after_value=sort_val, after_id=last_id
        )
        assert [t.title for t in page2_offset] == [t.title for t in page2_keyset]

    async def test_keyset_title_desc(self, db_session: AsyncSession) -> None:
        """Descending sort uses < operator for keyset."""
        await _insert_track(db_session, "Alpha")
        await _insert_track(db_session, "Beta")
        await _insert_track(db_session, "Gamma")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        page1, _, _, next_key = await track_repo.list_tracks(
            sort_by="title_desc", limit=2
        )
        assert [t.title for t in page1] == ["Gamma", "Beta"]
        assert next_key is not None

        sort_val, last_id = next_key
        page2, _, _, next_key2 = await track_repo.list_tracks(
            sort_by="title_desc", limit=2, after_value=sort_val, after_id=last_id
        )
        assert [t.title for t in page2] == ["Alpha"]
        assert next_key2 is None  # Last page

    async def test_keyset_duration_asc(self, db_session: AsyncSession) -> None:
        """Keyset works with integer sort column."""
        await _insert_track(db_session, "Short", duration_ms=120000)
        await _insert_track(db_session, "Medium", duration_ms=200000)
        await _insert_track(db_session, "Long", duration_ms=300000)

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        page1, _, _, next_key = await track_repo.list_tracks(
            sort_by="duration_asc", limit=2
        )
        assert [t.title for t in page1] == ["Short", "Medium"]
        assert next_key is not None

        sort_val, last_id = next_key
        page2, _, _, _ = await track_repo.list_tracks(
            sort_by="duration_asc", limit=2, after_value=sort_val, after_id=last_id
        )
        assert [t.title for t in page2] == ["Long"]

    async def test_keyset_last_page_returns_none(
        self, db_session: AsyncSession
    ) -> None:
        """When the page is not full, next_page_key is None (no more pages)."""
        await _insert_track(db_session, "Only Track")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        tracks, _, _, next_key = await track_repo.list_tracks(
            sort_by="title_asc", limit=50
        )
        assert len(tracks) == 1
        assert next_key is None

    async def test_keyset_with_search_filter(self, db_session: AsyncSession) -> None:
        """Keyset pagination respects active search filters."""
        await _insert_track(db_session, "Alpha Rock", artist="Band A")
        await _insert_track(db_session, "Beta Rock", artist="Band B")
        await _insert_track(db_session, "Gamma Jazz", artist="Band C")
        await _insert_track(db_session, "Delta Rock", artist="Band D")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Search "Rock" — should match 3 tracks
        page1, total, _, next_key = await track_repo.list_tracks(
            query="Rock", sort_by="title_asc", limit=2
        )
        assert total == 3
        assert [t.title for t in page1] == ["Alpha Rock", "Beta Rock"]
        assert next_key is not None

        sort_val, last_id = next_key
        page2, _, _, _ = await track_repo.list_tracks(
            query="Rock", sort_by="title_asc", limit=2,
            after_value=sort_val, after_id=last_id
        )
        assert [t.title for t in page2] == ["Delta Rock"]

    async def test_keyset_skips_offset_when_cursor_provided(
        self, db_session: AsyncSession
    ) -> None:
        """When keyset params are set, offset is ignored."""
        await _insert_track(db_session, "Alpha")
        await _insert_track(db_session, "Beta")
        await _insert_track(db_session, "Gamma")

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Get first page to extract cursor
        _, _, _, next_key = await track_repo.list_tracks(
            sort_by="title_asc", limit=1
        )
        assert next_key is not None
        sort_val, last_id = next_key

        # Pass both offset=999 and keyset — keyset should win
        tracks, _, _, _ = await track_repo.list_tracks(
            sort_by="title_asc", limit=2,
            offset=999, after_value=sort_val, after_id=last_id
        )
        assert [t.title for t in tracks] == ["Beta", "Gamma"]
