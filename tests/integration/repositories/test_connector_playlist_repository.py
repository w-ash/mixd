"""Integration tests for ConnectorPlaylistRepository with real database.

Covers the Epic-1 additions: snapshot_id round-trip through upsert_model,
and list_by_connector returns every cached row for a connector.
"""

from datetime import UTC, datetime
from uuid import uuid4

from src.domain.entities import ConnectorPlaylist
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


class TestSnapshotIdRoundTrip:
    """snapshot_id persists through upsert and re-emerges on read."""

    async def test_upsert_and_read_back(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        await repo.upsert_model(_cp(f"A {uid}", f"sp_{uid}", snapshot_id="snap-abc"))
        await db_session.commit()

        back = await repo.get_by_connector_id("spotify", f"sp_{uid}")
        assert back is not None
        assert back.snapshot_id == "snap-abc"

    async def test_upsert_updates_snapshot_on_change(self, db_session):
        """Second upsert with a new snapshot_id overwrites the first."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        await repo.upsert_model(_cp(f"A {uid}", f"sp_{uid}", snapshot_id="snap-v1"))
        await db_session.commit()

        await repo.upsert_model(_cp(f"A {uid}", f"sp_{uid}", snapshot_id="snap-v2"))
        await db_session.commit()

        back = await repo.get_by_connector_id("spotify", f"sp_{uid}")
        assert back is not None
        assert back.snapshot_id == "snap-v2"

    async def test_null_snapshot_is_preserved(self, db_session):
        """Playlists cached before snapshot tracking keep NULL on read."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        await repo.upsert_model(_cp(f"A {uid}", f"sp_{uid}", snapshot_id=None))
        await db_session.commit()

        back = await repo.get_by_connector_id("spotify", f"sp_{uid}")
        assert back is not None
        assert back.snapshot_id is None


class TestListByConnector:
    """list_by_connector returns every cached row for a connector."""

    async def test_returns_all_for_connector(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        await repo.upsert_model(_cp(f"Alpha {uid}", f"sp_a_{uid}"))
        await repo.upsert_model(_cp(f"Beta {uid}", f"sp_b_{uid}"))
        await db_session.commit()

        rows = await repo.list_by_connector("spotify")
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
        await db_session.commit()

        spotify_rows = await repo.list_by_connector("spotify")
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
        await db_session.commit()

        assert len(saved) == 3
        identifiers = {cp.connector_playlist_identifier for cp in saved}
        assert identifiers == {f"sp_a_{uid}", f"sp_b_{uid}", f"sp_c_{uid}"}

        # Round-trip: read each back, snapshot_id preserved.
        for ident, expected_snap in [
            (f"sp_a_{uid}", "s1"),
            (f"sp_b_{uid}", "s2"),
            (f"sp_c_{uid}", "s3"),
        ]:
            back = await repo.get_by_connector_id("spotify", ident)
            assert back is not None
            assert back.snapshot_id == expected_snap

    async def test_bulk_updates_existing_rows_on_conflict(self, db_session):
        """Re-upsert with new snapshot_id overwrites by (connector, identifier)."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        uid = uuid4().hex[:8]
        await repo.bulk_upsert_models([_cp(f"A {uid}", f"sp_{uid}", snapshot_id="v1")])
        await db_session.commit()

        await repo.bulk_upsert_models([_cp(f"A {uid}", f"sp_{uid}", snapshot_id="v2")])
        await db_session.commit()

        back = await repo.get_by_connector_id("spotify", f"sp_{uid}")
        assert back is not None
        assert back.snapshot_id == "v2"

    async def test_empty_batch_short_circuits(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_playlist_repository()

        result = await repo.bulk_upsert_models([])

        assert result == []
