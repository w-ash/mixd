"""Integration tests for ConnectorPlaylistRepository with real database.

Covers the Epic-1 additions: snapshot_id round-trip through upsert_model,
and the items-free summary projection, which is the only whole-connector
listing (browse surfaces need the item count, not the items). Also covers the
targeted find-by-identifier / find-by-id fetches every other caller uses.
"""

from datetime import UTC, datetime
from uuid import uuid4, uuid7

from src.domain.entities import ConnectorPlaylist, ConnectorPlaylistItem
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


def _cp(
    name: str, identifier: str, *, snapshot_id: str | None = None
) -> ConnectorPlaylist:
    return ConnectorPlaylist(
        connector_name="spotify",
        connector_playlist_identifier=identifier,
        name=name,
        description=None,
        owner="me",
        owner_id="me",
        is_public=True,
        collaborative=False,
        follower_count=None,
        items=[],
        raw_metadata={"total_tracks": 0, "images": []},
        snapshot_id=snapshot_id,
        last_updated=datetime.now(UTC),
    )


async def _read_back(repo, identifier: str) -> ConnectorPlaylist | None:
    """Fetch one cached playlist by its external identifier."""
    rows = await repo.find_by_identifiers("spotify", [identifier])
    return rows[0] if rows else None


class TestSnapshotIdRoundTrip:
    """snapshot_id persists through upsert and re-emerges on read."""

    async def test_upsert_and_read_back(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        identifier = f"sp_{uid}"
        await repo.upsert_model(_cp(f"A {uid}", identifier, snapshot_id="snap-abc"))
        await db_session.flush()

        back = await _read_back(repo, identifier)
        assert back is not None
        assert back.snapshot_id == "snap-abc"

    async def test_upsert_updates_snapshot_on_change(self, db_session):
        """Second upsert with a new snapshot_id overwrites the first."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        identifier = f"sp_{uid}"
        await repo.upsert_model(_cp(f"A {uid}", identifier, snapshot_id="snap-v1"))
        await db_session.flush()

        await repo.upsert_model(_cp(f"A {uid}", identifier, snapshot_id="snap-v2"))
        await db_session.flush()

        back = await _read_back(repo, identifier)
        assert back is not None
        assert back.snapshot_id == "snap-v2"

    async def test_null_snapshot_is_preserved(self, db_session):
        """Playlists cached before snapshot tracking keep NULL on read."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        identifier = f"sp_{uid}"
        await repo.upsert_model(_cp(f"A {uid}", identifier, snapshot_id=None))
        await db_session.flush()

        back = await _read_back(repo, identifier)
        assert back is not None
        assert back.snapshot_id is None


class TestConnectorScopedListing:
    """The summary listing returns every cached row for one connector."""

    async def test_returns_all_for_connector(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        await repo.upsert_model(_cp(f"Alpha {uid}", f"sp_a_{uid}"))
        await repo.upsert_model(_cp(f"Beta {uid}", f"sp_b_{uid}"))
        await db_session.flush()

        rows = await repo.list_summaries_by_connector("spotify")
        identifiers = {r.connector_playlist_identifier for r in rows}

        assert f"sp_a_{uid}" in identifiers
        assert f"sp_b_{uid}" in identifiers

    async def test_excludes_other_connectors(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        # Spotify row
        await repo.upsert_model(_cp(f"Spot {uid}", f"sp_{uid}"))
        # Last.fm row with different connector
        await repo.upsert_model(
            ConnectorPlaylist(
                connector_name="lastfm",
                connector_playlist_identifier=f"lf_{uid}",
                name=f"LF {uid}",
                description=None,
                owner=None,
                owner_id=None,
                is_public=True,
                collaborative=False,
                follower_count=None,
                items=[],
                raw_metadata={},
                snapshot_id=None,
                last_updated=datetime.now(UTC),
            )
        )
        await db_session.flush()

        spotify_rows = await repo.list_summaries_by_connector("spotify")
        connectors = {r.connector_name for r in spotify_rows}

        assert connectors == {"spotify"}


class TestBulkUpsertModels:
    """Single round-trip upsert of N playlists."""

    async def test_bulk_inserts_new_rows(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        batch = [
            _cp(f"A {uid}", f"sp_a_{uid}", snapshot_id="s1"),
            _cp(f"B {uid}", f"sp_b_{uid}", snapshot_id="s2"),
            _cp(f"C {uid}", f"sp_c_{uid}", snapshot_id="s3"),
        ]

        saved = await repo.bulk_upsert_models(batch)
        await db_session.flush()

        assert len(saved) == 3
        identifiers = {cp.connector_playlist_identifier for cp in saved}
        assert identifiers == {f"sp_a_{uid}", f"sp_b_{uid}", f"sp_c_{uid}"}

        # Round-trip: read each back, snapshot_id preserved.
        playlists = await repo.find_by_identifiers(
            "spotify", [f"sp_a_{uid}", f"sp_b_{uid}", f"sp_c_{uid}"]
        )
        playlist_by_ident = {p.connector_playlist_identifier: p for p in playlists}
        for ident, expected_snap in [
            (f"sp_a_{uid}", "s1"),
            (f"sp_b_{uid}", "s2"),
            (f"sp_c_{uid}", "s3"),
        ]:
            back = playlist_by_ident.get(ident)
            assert back is not None
            assert back.snapshot_id == expected_snap

    async def test_bulk_updates_existing_rows_on_conflict(self, db_session):
        """Re-upsert with new snapshot_id overwrites by (connector, identifier)."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        identifier = f"sp_{uid}"
        await repo.bulk_upsert_models([_cp(f"A {uid}", identifier, snapshot_id="v1")])
        await db_session.flush()

        await repo.bulk_upsert_models([_cp(f"A {uid}", identifier, snapshot_id="v2")])
        await db_session.flush()

        back = await _read_back(repo, identifier)
        assert back is not None
        assert back.snapshot_id == "v2"

    async def test_empty_batch_short_circuits(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        result = await repo.bulk_upsert_models([])

        assert result == []


def _item(identifier: str, position: int) -> ConnectorPlaylistItem:
    return ConnectorPlaylistItem(
        connector_track_identifier=identifier, position=position
    )


class TestListSummariesByConnector:
    """Summaries carry an item count instead of the items themselves."""

    async def test_item_count_matches_stored_items(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        identifier = f"sp_sum_{uid}"
        stored = _cp(f"Summary {uid}", identifier, snapshot_id="snap-sum")
        stored = ConnectorPlaylist(
            connector_name=stored.connector_name,
            connector_playlist_identifier=identifier,
            name=stored.name,
            description="a description",
            owner="me",
            owner_id="me",
            is_public=True,
            collaborative=False,
            follower_count=7,
            items=[_item(f"t{i}", i) for i in range(3)],
            raw_metadata={"total_tracks": 3},
            snapshot_id="snap-sum",
            last_updated=stored.last_updated,
        )
        await repo.upsert_model(stored)
        await db_session.flush()

        summaries = await repo.list_summaries_by_connector("spotify")
        back = next(
            (s for s in summaries if s.connector_playlist_identifier == identifier),
            None,
        )

        assert back is not None
        assert back.item_count == 3
        assert back.name == stored.name
        assert back.description == "a description"
        assert back.follower_count == 7
        assert back.snapshot_id == "snap-sum"
        assert back.raw_metadata == {"total_tracks": 3}
        assert back.id is not None

    async def test_empty_items_count_zero(self, db_session):
        """items defaults to [], so the count is 0 rather than NULL."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        identifier = f"sp_empty_{uid}"
        await repo.upsert_model(_cp(f"Empty {uid}", identifier))
        await db_session.flush()

        summaries = await repo.list_summaries_by_connector("spotify")
        back = next(
            (s for s in summaries if s.connector_playlist_identifier == identifier),
            None,
        )

        assert back is not None
        assert back.item_count == 0


class TestFindByIdentifiers:
    """Targeted fetch by external identifier, no full-connector scan."""

    async def test_returns_only_requested_subset(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        wanted = f"sp_a_{uid}"
        await repo.upsert_model(_cp(f"Alpha {uid}", wanted))
        await repo.upsert_model(_cp(f"Beta {uid}", f"sp_b_{uid}"))
        await db_session.flush()

        rows = await repo.find_by_identifiers("spotify", [wanted, f"sp_missing_{uid}"])

        assert [r.connector_playlist_identifier for r in rows] == [wanted]

    async def test_empty_identifiers_short_circuits(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        assert await repo.find_by_identifiers("spotify", []) == []

    async def test_excludes_other_connectors(self, db_session):
        """The same identifier on another connector is not returned."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        shared_identifier = f"shared_{uid}"
        await repo.upsert_model(_cp(f"Spot {uid}", shared_identifier))
        await repo.upsert_model(
            ConnectorPlaylist(
                connector_name="lastfm",
                connector_playlist_identifier=shared_identifier,
                name=f"LF {uid}",
                last_updated=datetime.now(UTC),
            )
        )
        await db_session.flush()

        rows = await repo.find_by_identifiers("lastfm", [shared_identifier])

        assert [r.connector_name for r in rows] == ["lastfm"]


class TestFindByIds:
    """Targeted fetch by internal ID."""

    async def test_returns_requested_rows(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        first = await repo.upsert_model(_cp(f"Alpha {uid}", f"sp_a_{uid}"))
        await repo.upsert_model(_cp(f"Beta {uid}", f"sp_b_{uid}"))
        await db_session.flush()

        rows = await repo.find_by_ids([first.id])

        assert [r.id for r in rows] == [first.id]

    async def test_empty_ids_short_circuits(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        assert await repo.find_by_ids([]) == []

    async def test_unknown_id_is_absent(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        assert await repo.find_by_ids([uuid7()]) == []
