"""Integration tests for PlaylistReconciliationEngine against real persistence.

The unit tests mock the repos; these exercise the full pull stack on a real DB —
fetch (mocked network) → resolve/ingest → upsert canonical → record base — and
prove a re-run with unchanged remote is an idempotent no-op (the import-no-op bug
fix: the old code skipped on snapshot presence; the engine actually compares).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.playlist_reconciliation_engine import (
    PlaylistReconciliationEngine,
)
from src.domain.entities.playlist import ConnectorPlaylist, ConnectorPlaylistItem
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.entities.track import Artist, ConnectorTrack
from src.domain.playlist.diff_engine import PlaylistOpsOutcome
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlaylist,
    DBPlaylist,
    DBPlaylistMapping,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_mock_metric_config

_ENGINE_MOD = "src.application.services.playlist_reconciliation_engine"
_PUSH_MOD = "src.application.services.connector_push"
_RESOLVER_MOD = "src.application.use_cases._shared.connector_resolver"


async def _make_link(session: AsyncSession, external_id: str) -> PlaylistLink:
    now = datetime.now(UTC)
    playlist = DBPlaylist(name="Canon", track_count=0, created_at=now, updated_at=now)
    connector_playlist = DBConnectorPlaylist(
        connector_name="spotify",
        connector_playlist_identifier=external_id,
        name="External",
        is_public=False,
        items=[],
        raw_metadata={},
        last_updated=now,
        created_at=now,
        updated_at=now,
    )
    session.add_all([playlist, connector_playlist])
    await session.flush()
    mapping = DBPlaylistMapping(
        user_id="default",
        playlist_id=playlist.id,
        connector_name="spotify",
        connector_playlist_id=connector_playlist.id,
        sync_direction="pull",
        created_at=now,
        updated_at=now,
    )
    session.add(mapping)
    await session.flush()
    return PlaylistLink(
        id=mapping.id,
        playlist_id=playlist.id,
        connector_name="spotify",
        connector_playlist_identifier=external_id,
        sync_direction=SyncDirection.PULL,
    )


def _remote(external_id: str, track_ids: list[str], snapshot: str) -> ConnectorPlaylist:
    return ConnectorPlaylist(
        connector_name="spotify",
        connector_playlist_identifier=external_id,
        name="External",
        snapshot_id=snapshot,
        items=[
            ConnectorPlaylistItem(
                connector_track_identifier=tid,
                position=i,
                extras={
                    "full_track_data": {
                        "id": tid,
                        "name": f"Track {tid}",
                        "artists": [{"name": f"Artist {tid}"}],
                    }
                },
            )
            for i, tid in enumerate(track_ids)
        ],
    )


def _fake_conversion_connector() -> MagicMock:
    conn = MagicMock()

    def convert(data: dict) -> ConnectorTrack:
        return ConnectorTrack(
            connector_name="spotify",
            connector_track_identifier=data["id"],
            title=data.get("name") or "Untitled",
            artists=[Artist(name=a["name"]) for a in data.get("artists", [])],
        )

    conn.convert_track_to_connector.side_effect = convert
    return conn


async def _canonical_track_count(session: AsyncSession, playlist_id: UUID) -> int:
    uow = get_unit_of_work(session)
    playlist = await uow.get_playlist_repository().get_playlist_by_id(
        playlist_id, user_id="default"
    )
    return len(playlist.entries)


class TestPullRoundTrip:
    async def test_pull_populates_canonical_and_records_base(self, db_session):
        link = await _make_link(db_session, "ext-pull-1")
        remote = _remote("ext-pull-1", ["t1", "t2", "t3"], snapshot="snap1")
        engine = PlaylistReconciliationEngine(metric_config=make_mock_metric_config())

        with (
            patch(f"{_ENGINE_MOD}.sync_connector_playlist", return_value=remote),
            patch(
                f"{_RESOLVER_MOD}.resolve_track_conversion_connector",
                return_value=_fake_conversion_connector(),
            ),
        ):
            result = await engine.apply(
                link,
                SyncDirection.PULL,
                get_unit_of_work(db_session),
                user_id="default",
            )

        assert result.skipped is False
        # Canonical now holds the 3 fetched tracks.
        assert await _canonical_track_count(db_session, link.playlist_id) == 3
        # Base recorded with the remote snapshot.
        base = (
            await get_unit_of_work(db_session)
            .get_playlist_sync_base_repository()
            .get_for_link(link.id)
        )
        assert base is not None
        assert base.base_snapshot_id == "snap1"

    async def test_unchanged_remote_second_pull_is_noop(self, db_session):
        link = await _make_link(db_session, "ext-pull-2")
        remote = _remote("ext-pull-2", ["t1", "t2"], snapshot="snap1")
        engine = PlaylistReconciliationEngine(metric_config=make_mock_metric_config())

        with (
            patch(f"{_ENGINE_MOD}.sync_connector_playlist", return_value=remote),
            patch(
                f"{_RESOLVER_MOD}.resolve_track_conversion_connector",
                return_value=_fake_conversion_connector(),
            ),
        ):
            first = await engine.apply(
                link,
                SyncDirection.PULL,
                get_unit_of_work(db_session),
                user_id="default",
            )
            # Second pull with the SAME remote → nothing to change.
            second = await engine.apply(
                link,
                SyncDirection.PULL,
                get_unit_of_work(db_session),
                user_id="default",
            )

        assert first.skipped is False
        assert second.skipped is True
        assert await _canonical_track_count(db_session, link.playlist_id) == 2


class TestPushRoundTrip:
    async def test_push_executes_and_records_post_push_base(self, db_session):
        # Pull first to populate the canonical + connector_tracks (so the remote
        # items resolve on a real DB), then push against a remote MISSING one track
        # → the push adds it, and the base is recorded from the POST-push snapshot.
        link = await _make_link(db_session, "ext-push-1")
        connector = MagicMock()
        connector.execute_playlist_operations = AsyncMock(
            return_value=PlaylistOpsOutcome(
                snapshot_id="after-push", requested=1, failed=0
            )
        )
        engine = PlaylistReconciliationEngine(metric_config=make_mock_metric_config())

        with (
            patch(
                f"{_RESOLVER_MOD}.resolve_track_conversion_connector",
                return_value=_fake_conversion_connector(),
            ),
            patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector),
        ):
            full = _remote("ext-push-1", ["t1", "t2", "t3"], snapshot="snap1")
            with patch(f"{_ENGINE_MOD}.sync_connector_playlist", return_value=full):
                await engine.apply(
                    link,
                    SyncDirection.PULL,
                    get_unit_of_work(db_session),
                    user_id="default",
                )
            # Remote is missing t3 → push diff adds it.
            partial = _remote("ext-push-1", ["t1", "t2"], snapshot="snap2")
            with patch(f"{_ENGINE_MOD}.sync_connector_playlist", return_value=partial):
                result = await engine.apply(
                    link,
                    SyncDirection.PUSH,
                    get_unit_of_work(db_session),
                    user_id="default",
                )

        assert result.skipped is False
        assert result.direction == SyncDirection.PUSH
        connector.execute_playlist_operations.assert_awaited_once()
        base = (
            await get_unit_of_work(db_session)
            .get_playlist_sync_base_repository()
            .get_for_link(link.id)
        )
        assert base is not None
        assert base.base_snapshot_id == "after-push"
