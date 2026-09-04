"""Unit tests for sync_likes use cases — incremental commit and progress behaviour.

Verifies that ImportLikesUseCase and ExportLovesUseCase commit each batch
incrementally via commit_batch(), maintain sync checkpoints, emit progress
events, and thread the command's connector through every service-scoped call.
Both use cases are connector-agnostic; these tests drive them with Spotify
(import) and Last.fm (export), the two wired connectors.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.application.use_cases.sync_likes import (
    _CHECKPOINT_LOOKBACK,
    CHECKPOINT_COMBINATIONS,
    ExportLovesCommand,
    ExportLovesUseCase,
    GetSyncCheckpointStatusUseCase,
    ImportLikesCommand,
    ImportLikesUseCase,
)
from src.domain.entities import ConnectorTrack, Track
from src.domain.entities.track import Artist
from tests.fixtures import (
    make_connector_track,
    make_mock_connector_provider,
    make_mock_uow,
)


def _page_of_tracks(
    count: int, page: int, *, cursor: str | None = "next", total: int | None = 100
) -> tuple[list[ConnectorTrack], str | None, int | None]:
    """Build a fake page of connector tracks with a cursor and total."""
    return (
        [make_connector_track(f"sp_{page}_{i}") for i in range(count)],
        cursor,
        total,
    )


class TestImportLikesIncrementalCommit:
    """Verify commit_batch() is called per batch and checkpoints advance."""

    @pytest.fixture
    def mock_uow(self):
        uow = make_mock_uow()
        # Checkpoint repo: no existing checkpoint
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint_repo.get_sync_checkpoint = AsyncMock(return_value=None)
        checkpoint_repo.save_sync_checkpoint = AsyncMock(side_effect=lambda cp: cp)
        # Connector repo: all tracks are new
        connector_repo = uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors = AsyncMock(return_value={})
        connector_repo.ingest_external_tracks_bulk = AsyncMock(
            side_effect=lambda _svc, tracks, **kw: [
                Track(
                    id=i + 1,
                    title=t.title,
                    artists=[Artist(name="A")],
                    connector_track_identifiers={
                        "spotify": t.connector_track_identifier
                    },
                )
                for i, t in enumerate(tracks)
            ]
        )
        # Like repo
        like_repo = uow.get_like_repository()
        like_repo.save_track_likes_batch = AsyncMock(return_value=[])
        return uow

    def _mock_connector(self, pages: list[tuple[list[ConnectorTrack], str | None]]):
        """Create a mock connector that returns pages sequentially."""
        connector = AsyncMock()
        connector.get_liked_tracks = AsyncMock(side_effect=pages)
        return connector

    async def test_commit_batch_called_per_page(self, mock_uow):
        """3 pages of tracks → commit_batch called 3 times."""
        pages = [
            _page_of_tracks(5, 0, cursor="c1"),
            _page_of_tracks(5, 1, cursor="c2"),
            _page_of_tracks(5, 2, cursor=None),
        ]
        connector = self._mock_connector(pages)
        with patch(
            "src.application.use_cases.sync_likes.resolve_liked_track_connector",
            return_value=connector,
        ):
            use_case = ImportLikesUseCase()
            command = ImportLikesCommand(
                user_id="test-user", connector="spotify", limit=50
            )
            await use_case.execute(command, mock_uow)

        assert mock_uow.commit_batch.await_count == 3
        assert mock_uow.commit.await_count == 1  # final commit

    async def test_checkpoint_updated_every_batch(self, mock_uow):
        """Checkpoint should be saved every batch, not every 10."""
        pages = [
            _page_of_tracks(5, 0, cursor="c1"),
            _page_of_tracks(5, 1, cursor="c2"),
            _page_of_tracks(5, 2, cursor=None),
        ]
        connector = self._mock_connector(pages)
        with patch(
            "src.application.use_cases.sync_likes.resolve_liked_track_connector",
            return_value=connector,
        ):
            use_case = ImportLikesUseCase()
            command = ImportLikesCommand(
                user_id="test-user", connector="spotify", limit=50
            )
            await use_case.execute(command, mock_uow)

        checkpoint_repo = mock_uow.get_checkpoint_repository()
        # 3 per-batch checkpoints + 1 final checkpoint on exit = 4
        assert checkpoint_repo.save_sync_checkpoint.await_count == 4

    async def test_contention_fails_the_run_before_the_checkpoint_advances(
        self, mock_uow
    ):
        """A swallowed 55P03 would advance the cursor past the failed page,
        permanently skipping its likes; re-raising lets the next sync rewrite it."""

        class _LockNotAvailable(Exception):
            sqlstate = "55P03"

        like_repo = mock_uow.get_like_repository()
        like_repo.save_track_likes_batch = AsyncMock(
            side_effect=_LockNotAvailable("key held by concurrent writer")
        )
        connector = self._mock_connector([_page_of_tracks(5, 0, cursor=None)])
        with patch(
            "src.application.use_cases.sync_likes.resolve_liked_track_connector",
            return_value=connector,
        ):
            use_case = ImportLikesUseCase()
            command = ImportLikesCommand(
                user_id="test-user", connector="spotify", limit=50
            )
            with pytest.raises(_LockNotAvailable):
                await use_case.execute(command, mock_uow)

        checkpoint_repo = mock_uow.get_checkpoint_repository()
        checkpoint_repo.save_sync_checkpoint.assert_not_awaited()
        mock_uow.commit_batch.assert_not_awaited()

    async def test_cancellation_preserves_committed_batches(self, mock_uow):
        """CancelledError after page 2 of 4 → commit_batch called exactly 2 times."""
        import asyncio

        pages = [
            _page_of_tracks(5, 0, cursor="c1"),
            _page_of_tracks(5, 1, cursor="c2"),
            asyncio.CancelledError(),
        ]
        connector = AsyncMock()
        connector.get_liked_tracks = AsyncMock(side_effect=pages)
        with patch(
            "src.application.use_cases.sync_likes.resolve_liked_track_connector",
            return_value=connector,
        ):
            use_case = ImportLikesUseCase()
            command = ImportLikesCommand(
                user_id="test-user", connector="spotify", limit=50
            )
            with pytest.raises(asyncio.CancelledError):
                await use_case.execute(command, mock_uow)

        assert mock_uow.commit_batch.await_count == 2

    async def test_empty_response_no_commit_batch(self, mock_uow):
        """Empty first page → commit_batch never called."""
        connector = self._mock_connector([([], None, 0)])
        with patch(
            "src.application.use_cases.sync_likes.resolve_liked_track_connector",
            return_value=connector,
        ):
            use_case = ImportLikesUseCase()
            command = ImportLikesCommand(
                user_id="test-user", connector="spotify", limit=50
            )
            await use_case.execute(command, mock_uow)

        assert mock_uow.commit_batch.await_count == 0


class TestImportLikesForceMode:
    """Verify force mode bypasses early stop and cursor resume works."""

    @pytest.fixture
    def mock_uow(self):
        uow = make_mock_uow()
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint_repo.get_sync_checkpoint = AsyncMock(return_value=None)
        checkpoint_repo.save_sync_checkpoint = AsyncMock(side_effect=lambda cp: cp)
        # Like repo: all existing tracks are fully synced (spotify + mixd)
        like_repo = uow.get_like_repository()
        like_repo.get_liked_status_batch = AsyncMock(
            side_effect=lambda ids, services, **kw: {
                tid: dict.fromkeys(services, True) for tid in ids
            }
        )
        like_repo.save_track_likes_batch = AsyncMock(return_value=[])
        return uow

    async def test_force_bypasses_early_stop(self, mock_uow):
        """Force mode pages through all-duplicate batch to reach new tracks."""
        dup_page = _page_of_tracks(5, 0, cursor="c1")
        new_page = _page_of_tracks(5, 1, cursor=None)

        # Page 1: all duplicates (already synced). Page 2: all new.
        call_count = 0

        async def _get_liked(limit, cursor=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return dup_page
            return new_page

        connector = AsyncMock()
        connector.get_liked_tracks = AsyncMock(side_effect=_get_liked)

        # First call → existing tracks; second call → new tracks
        connector_repo = mock_uow.get_connector_repository()
        dup_tracks = dup_page[0]
        existing_map = {
            ("spotify", ct.connector_track_identifier): Track(
                id=i + 1000, title=ct.title, artists=[Artist(name="A")]
            )
            for i, ct in enumerate(dup_tracks)
        }

        find_calls = 0

        async def _find_by_connectors(connections, **kw):
            nonlocal find_calls
            find_calls += 1
            if find_calls == 1:
                return existing_map
            return {}

        connector_repo.find_tracks_by_connectors = AsyncMock(
            side_effect=_find_by_connectors
        )
        connector_repo.ingest_external_tracks_bulk = AsyncMock(
            side_effect=lambda _svc, tracks, **kw: [
                Track(
                    id=i + 2000,
                    title=t.title,
                    artists=[Artist(name="A")],
                    connector_track_identifiers={
                        "spotify": t.connector_track_identifier
                    },
                )
                for i, t in enumerate(tracks)
            ]
        )

        with patch(
            "src.application.use_cases.sync_likes.resolve_liked_track_connector",
            return_value=connector,
        ):
            use_case = ImportLikesUseCase()
            command = ImportLikesCommand(
                user_id="test-user", connector="spotify", limit=50, force=True
            )
            result = await use_case.execute(command, mock_uow)

        # Both pages processed — force mode didn't early stop on page 1
        assert connector.get_liked_tracks.await_count == 2

    async def test_default_triggers_early_stop(self, mock_uow):
        """Without force, all-duplicate batch triggers early stop."""
        dup_page = _page_of_tracks(5, 0, cursor="c1")
        new_page = _page_of_tracks(5, 1, cursor=None)

        call_count = 0

        async def _get_liked(limit, cursor=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return dup_page
            return new_page

        connector = AsyncMock()
        connector.get_liked_tracks = AsyncMock(side_effect=_get_liked)

        # All tracks in page 1 are existing + fully liked
        connector_repo = mock_uow.get_connector_repository()
        dup_tracks = dup_page[0]
        existing_map = {
            ("spotify", ct.connector_track_identifier): Track(
                id=i + 1000, title=ct.title, artists=[Artist(name="A")]
            )
            for i, ct in enumerate(dup_tracks)
        }
        connector_repo.find_tracks_by_connectors = AsyncMock(return_value=existing_map)
        connector_repo.ingest_external_tracks_bulk = AsyncMock(return_value=[])

        with patch(
            "src.application.use_cases.sync_likes.resolve_liked_track_connector",
            return_value=connector,
        ):
            use_case = ImportLikesUseCase()
            command = ImportLikesCommand(
                user_id="test-user", connector="spotify", limit=50, force=False
            )
            result = await use_case.execute(command, mock_uow)

        # Early stop after page 1 — page 2 never fetched
        assert connector.get_liked_tracks.await_count == 1

    async def test_cursor_saved_on_fetch_error(self, mock_uow):
        """Fetch error saves checkpoint with cursor before re-raising."""
        connector = AsyncMock()
        connector.get_liked_tracks = AsyncMock(side_effect=RuntimeError("API failed"))

        use_case = ImportLikesUseCase()
        command = ImportLikesCommand(user_id="test-user", connector="spotify", limit=50)
        with (
            patch(
                "src.application.use_cases.sync_likes.resolve_liked_track_connector",
                return_value=connector,
            ),
            pytest.raises(RuntimeError, match="API failed"),
        ):
            await use_case.execute(command, mock_uow)

        # Checkpoint was saved (progress preserved) and committed
        checkpoint_repo = mock_uow.get_checkpoint_repository()
        assert checkpoint_repo.save_sync_checkpoint.await_count >= 1
        assert mock_uow.commit.await_count >= 1

    async def test_cursor_resumed_from_checkpoint(self, mock_uow):
        """Import resumes from saved checkpoint cursor."""
        from src.domain.entities.operations import SyncCheckpoint

        saved_checkpoint = SyncCheckpoint(
            user_id="test-user",
            service="spotify",
            entity_type="likes",
            cursor="100",  # Resume from offset 100
        )
        checkpoint_repo = mock_uow.get_checkpoint_repository()
        checkpoint_repo.get_or_create_sync_checkpoint = AsyncMock(
            return_value=saved_checkpoint
        )

        new_page = _page_of_tracks(5, 0, cursor=None)
        connector = AsyncMock()
        connector.get_liked_tracks = AsyncMock(return_value=new_page)

        connector_repo = mock_uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors = AsyncMock(return_value={})
        connector_repo.ingest_external_tracks_bulk = AsyncMock(
            side_effect=lambda _svc, tracks, **kw: [
                Track(
                    id=i + 2000,
                    title=t.title,
                    artists=[Artist(name="A")],
                    connector_track_identifiers={
                        "spotify": t.connector_track_identifier
                    },
                )
                for i, t in enumerate(tracks)
            ]
        )

        with patch(
            "src.application.use_cases.sync_likes.resolve_liked_track_connector",
            return_value=connector,
        ):
            use_case = ImportLikesUseCase()
            command = ImportLikesCommand(
                user_id="test-user", connector="spotify", limit=50
            )
            await use_case.execute(command, mock_uow)

        # Connector was called with the saved cursor, not None
        connector.get_liked_tracks.assert_awaited_once_with(limit=50, cursor="100")


class TestExportLovesIncrementalCommit:
    """Verify commit_batch() is called per batch in Last.fm export."""

    @pytest.fixture
    def mock_uow(self):
        uow = make_mock_uow()
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint_repo.get_sync_checkpoint = AsyncMock(return_value=None)
        checkpoint_repo.save_sync_checkpoint = AsyncMock(side_effect=lambda cp: cp)
        return uow

    async def test_commit_batch_called_per_export_batch(self, mock_uow):
        """3 batches of unsynced likes → commit_batch called 3 times."""
        like_repo = mock_uow.get_like_repository()
        track_repo = mock_uow.get_track_repository()

        # 15 unsynced likes, batch_size=5 → 3 batches
        from src.domain.entities import TrackLike

        unsynced = [
            TrackLike(track_id=i, service="spotify", user_id="test-user", is_liked=True)
            for i in range(1, 16)
        ]
        like_repo.get_unsynced_likes = AsyncMock(return_value=unsynced)

        # Track lookups
        tracks_map = {
            i: Track(id=i, title=f"Track {i}", artists=[Artist(name="A")])
            for i in range(1, 16)
        }
        track_repo.find_tracks_by_ids = AsyncMock(return_value=tracks_map)

        # Mock lastfm connector: one call per batch, one flag per input
        lastfm = AsyncMock()
        lastfm.love_tracks = AsyncMock(side_effect=lambda items: [True] * len(items))

        with patch(
            "src.application.use_cases.sync_likes.resolve_love_track_connector",
            return_value=lastfm,
        ):
            use_case = ExportLovesUseCase()
            command = ExportLovesCommand(
                user_id="test-user", connector="lastfm", batch_size=5
            )
            await use_case.execute(command, mock_uow)

        assert mock_uow.commit_batch.await_count == 3
        assert mock_uow.commit.await_count == 1


class TestExportLovesPartialFailure:
    """A per-item failure must leave a readable reason on the result.

    ``is_failure`` flips on the ``errors`` summary metric, but ``failure_message``
    reads only top-level ``metadata["error"]`` — without it the audit row records
    "errors: N" with no message.
    """

    @pytest.fixture
    def mock_uow(self):
        uow = make_mock_uow()
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint_repo.get_sync_checkpoint = AsyncMock(return_value=None)
        checkpoint_repo.save_sync_checkpoint = AsyncMock(side_effect=lambda cp: cp)
        return uow

    async def _export(self, mock_uow, lastfm):
        from src.domain.entities import TrackLike

        like_repo = mock_uow.get_like_repository()
        track_repo = mock_uow.get_track_repository()
        unsynced = [
            TrackLike(track_id=i, service="spotify", user_id="test-user", is_liked=True)
            for i in range(1, 3)
        ]
        like_repo.get_unsynced_likes = AsyncMock(return_value=unsynced)
        track_repo.find_tracks_by_ids = AsyncMock(
            return_value={
                i: Track(id=i, title=f"Track {i}", artists=[Artist(name="A")])
                for i in range(1, 3)
            }
        )

        with patch(
            "src.application.use_cases.sync_likes.resolve_love_track_connector",
            return_value=lastfm,
        ):
            use_case = ExportLovesUseCase()
            command = ExportLovesCommand(
                user_id="test-user", connector="lastfm", batch_size=5
            )
            return await use_case.execute(command, mock_uow)

    async def test_batch_error_produces_failure_message(self, mock_uow):
        lastfm = AsyncMock()
        lastfm.love_tracks = AsyncMock(side_effect=RuntimeError("Last.fm 429"))

        result = await self._export(mock_uow, lastfm)

        assert result.is_failure
        message = result.failure_message
        assert message is not None
        assert "2 of 2 likes failed to export" in message
        assert "Last.fm 429" in message

    async def test_clean_export_records_no_failure_message(self, mock_uow):
        lastfm = AsyncMock()
        lastfm.love_tracks = AsyncMock(side_effect=lambda items: [True] * len(items))

        result = await self._export(mock_uow, lastfm)

        assert not result.is_failure
        assert result.failure_message is None

    async def test_all_false_reports_every_item_as_error(self, mock_uow):
        lastfm = AsyncMock()
        lastfm.love_tracks = AsyncMock(side_effect=lambda items: [False] * len(items))

        result = await self._export(mock_uow, lastfm)

        assert result.is_failure
        assert result.summary_metrics.get("errors") == 2
        message = result.failure_message
        assert message is not None
        assert "2 of 2 likes failed to export" in message


def _provider_uow(name: str, display_name: str):
    """UoW whose registry descriptor carries a specific display name."""
    from src.domain.entities.connector import ConnectorDescriptor

    provider = make_mock_connector_provider(name=name)
    provider.describe.return_value = ConnectorDescriptor(
        name=name,
        display_name=display_name,
        category="streaming",
        auth_method="oauth",
        capabilities=frozenset({"likes_import", "love_tracks"}),
    )
    uow = make_mock_uow(connector_provider=provider)
    checkpoint_repo = uow.get_checkpoint_repository()
    checkpoint_repo.get_sync_checkpoint = AsyncMock(return_value=None)
    checkpoint_repo.save_sync_checkpoint = AsyncMock(side_effect=lambda cp: cp)
    return uow


class TestOperationNameParity:
    """The persisted operation names must survive the connector-agnostic rewrite.

    They key the audit log and the web operation history, so a changed string
    orphans every existing row.
    """

    async def test_import_result_name_comes_from_the_descriptor(self) -> None:
        uow = _provider_uow("spotify", "Spotify")
        uow.get_connector_repository().find_tracks_by_connectors = AsyncMock(
            return_value={}
        )
        connector = AsyncMock()
        connector.get_liked_tracks = AsyncMock(return_value=([], None, 0))

        with patch(
            "src.application.use_cases.sync_likes.resolve_liked_track_connector",
            return_value=connector,
        ):
            result = await ImportLikesUseCase().execute(
                ImportLikesCommand(user_id="u", connector="spotify"), uow
            )

        assert result.operation_name == "Spotify Likes Import"

    async def test_export_result_name_comes_from_the_descriptor(self) -> None:
        uow = _provider_uow("lastfm", "Last.fm")
        uow.get_like_repository().get_unsynced_likes = AsyncMock(return_value=[])
        uow.get_like_repository().count_liked_tracks = AsyncMock(return_value=0)
        lastfm = AsyncMock()

        with patch(
            "src.application.use_cases.sync_likes.resolve_love_track_connector",
            return_value=lastfm,
        ):
            result = await ExportLovesUseCase().execute(
                ExportLovesCommand(user_id="u", connector="lastfm"), uow
            )

        assert result.operation_name == "Last.fm Likes Export"


class TestExportBatching:
    """One connector call and one like write per batch, keyed by the connector."""

    @pytest.fixture
    def mock_uow(self):
        from src.domain.entities import TrackLike

        uow = _provider_uow("lastfm", "Last.fm")
        like_repo = uow.get_like_repository()
        like_repo.get_unsynced_likes = AsyncMock(
            return_value=[
                TrackLike(track_id=i, service="mixd", user_id="u", is_liked=True)
                for i in range(1, 4)
            ]
        )
        like_repo.count_liked_tracks = AsyncMock(return_value=3)
        like_repo.save_track_likes_batch = AsyncMock(return_value=[])
        uow.get_track_repository().find_tracks_by_ids = AsyncMock(
            return_value={
                i: Track(id=i, title=f"Track {i}", artists=[Artist(name="A")])
                for i in range(1, 4)
            }
        )
        return uow

    async def _export(self, mock_uow, lastfm):
        with patch(
            "src.application.use_cases.sync_likes.resolve_love_track_connector",
            return_value=lastfm,
        ):
            return await ExportLovesUseCase().execute(
                ExportLovesCommand(user_id="u", connector="lastfm", batch_size=10),
                mock_uow,
            )

    async def test_one_call_per_batch_and_one_like_write(self, mock_uow) -> None:
        lastfm = AsyncMock()
        lastfm.love_tracks = AsyncMock(return_value=[True, True, True])

        result = await self._export(mock_uow, lastfm)

        lastfm.love_tracks.assert_awaited_once_with([
            ("A", "Track 1"),
            ("A", "Track 2"),
            ("A", "Track 3"),
        ])
        like_repo = mock_uow.get_like_repository()
        like_repo.save_track_likes_batch.assert_awaited_once()
        entries, _ = like_repo.save_track_likes_batch.await_args
        assert [(track_id, service) for track_id, service, *_ in entries[0]] == [
            (1, "lastfm"),
            (2, "lastfm"),
            (3, "lastfm"),
        ]
        assert result.summary_metrics.get("exported") == 3

    async def test_false_result_is_an_error(self, mock_uow) -> None:
        lastfm = AsyncMock()
        lastfm.love_tracks = AsyncMock(return_value=[True, False, True])

        result = await self._export(mock_uow, lastfm)

        assert result.summary_metrics.get("exported") == 2
        assert result.summary_metrics.get("errors") == 1
        assert result.is_failure
        message = result.failure_message
        assert message is not None
        assert "connector rejected the love" in message
        # Only the two that stuck are written back
        entries, _ = mock_uow.get_like_repository().save_track_likes_batch.await_args
        assert [track_id for track_id, *_ in entries[0]] == [1, 3]


class TestExportCheckpointWatermark:
    """The export checkpoint is a low watermark over outstanding likes."""

    @pytest.fixture
    def mock_uow(self):
        from src.domain.entities import SyncCheckpoint

        uow = _provider_uow("lastfm", "Last.fm")
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint_repo.get_or_create_sync_checkpoint = AsyncMock(
            return_value=SyncCheckpoint(
                user_id="u",
                service="lastfm",
                entity_type="likes",
                last_timestamp=datetime(2026, 8, 12, tzinfo=UTC),
            )
        )
        like_repo = uow.get_like_repository()
        like_repo.count_liked_tracks = AsyncMock(return_value=3)
        like_repo.save_track_likes_batch = AsyncMock(return_value=[])
        uow.get_track_repository().find_tracks_by_ids = AsyncMock(
            side_effect=lambda ids: {
                i: Track(id=i, title=f"Track {i}", artists=[Artist(name="A")])
                for i in ids
            }
        )
        return uow

    async def _export(self, mock_uow, lastfm, *, batch_size: int = 1):
        with patch(
            "src.application.use_cases.sync_likes.resolve_love_track_connector",
            return_value=lastfm,
        ):
            return await ExportLovesUseCase().execute(
                ExportLovesCommand(
                    user_id="u", connector="lastfm", batch_size=batch_size
                ),
                mock_uow,
            )

    async def test_failed_like_pulls_the_watermark_backwards(self, mock_uow):
        """One old failure rewinds the mark past two newer successes."""
        from src.domain.entities import TrackLike

        t1 = datetime(2026, 8, 10, tzinfo=UTC)
        t2 = datetime(2026, 8, 11, tzinfo=UTC)
        t3 = datetime(2026, 8, 12, tzinfo=UTC)
        mock_uow.get_like_repository().get_unsynced_likes = AsyncMock(
            return_value=[
                TrackLike(
                    track_id=i,
                    service="mixd",
                    user_id="u",
                    is_liked=True,
                    updated_at=ts,
                )
                for i, ts in [(1, t1), (2, t2), (3, t3)]
            ]
        )
        lastfm = AsyncMock()
        # Only the oldest like (Track 1) fails; the two newer ones succeed.
        lastfm.love_tracks = AsyncMock(
            side_effect=lambda items: [items != [("A", "Track 1")]]
        )

        await self._export(mock_uow, lastfm)

        checkpoint_repo = mock_uow.get_checkpoint_repository()
        assert checkpoint_repo.save_sync_checkpoint.await_count == 1
        saved = checkpoint_repo.save_sync_checkpoint.await_args.args[0]
        assert saved.last_timestamp == t1 - _CHECKPOINT_LOOKBACK
        # Moved backwards past the pre-run checkpoint, not forward to now
        assert saved.last_timestamp < datetime(2026, 8, 12, tzinfo=UTC)

    async def test_clean_run_writes_run_start_minus_lookback(self, mock_uow):
        """An all-clean run advances the watermark to the run start."""
        from src.domain.entities import TrackLike

        mock_uow.get_like_repository().get_unsynced_likes = AsyncMock(
            return_value=[
                TrackLike(
                    track_id=1,
                    service="mixd",
                    user_id="u",
                    is_liked=True,
                    updated_at=datetime(2026, 8, 10, tzinfo=UTC),
                )
            ]
        )
        lastfm = AsyncMock()
        lastfm.love_tracks = AsyncMock(side_effect=lambda items: [True] * len(items))

        before = datetime.now(UTC)
        await self._export(mock_uow, lastfm)
        after = datetime.now(UTC)

        checkpoint_repo = mock_uow.get_checkpoint_repository()
        saved = checkpoint_repo.save_sync_checkpoint.await_args.args[0]
        assert before - _CHECKPOINT_LOOKBACK <= saved.last_timestamp
        assert saved.last_timestamp <= after - _CHECKPOINT_LOOKBACK


class TestCheckpointStatusBatchRead:
    """``execute_all`` reads the whole surface in two queries, not per combination."""

    @pytest.fixture
    def mock_uow(self):
        from datetime import UTC, datetime

        from src.domain.entities import SyncCheckpoint

        uow = make_mock_uow()
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint_repo.list_for_user = AsyncMock(
            return_value=[
                SyncCheckpoint(
                    user_id="u",
                    service="spotify",
                    entity_type="likes",
                    last_timestamp=datetime(2026, 8, 1, tzinfo=UTC),
                ),
                SyncCheckpoint(user_id="u", service="lastfm", entity_type="likes"),
            ]
        )
        uow.get_like_repository().count_liked_tracks_by_service = AsyncMock(
            return_value={"spotify": 42, "lastfm": 7}
        )
        return uow

    async def test_two_queries_cover_every_combination(self, mock_uow) -> None:
        statuses = await GetSyncCheckpointStatusUseCase().execute_all(
            "u", CHECKPOINT_COMBINATIONS, mock_uow
        )

        checkpoint_repo = mock_uow.get_checkpoint_repository()
        checkpoint_repo.list_for_user.assert_awaited_once_with("u")
        checkpoint_repo.get_sync_checkpoint.assert_not_awaited()
        like_repo = mock_uow.get_like_repository()
        like_repo.count_liked_tracks_by_service.assert_awaited_once()
        like_repo.count_liked_tracks.assert_not_awaited()
        assert len(statuses) == len(CHECKPOINT_COMBINATIONS)

    async def test_local_count_only_for_likes_with_a_checkpoint(self, mock_uow) -> None:
        statuses = await GetSyncCheckpointStatusUseCase().execute_all(
            "u", CHECKPOINT_COMBINATIONS, mock_uow
        )

        by_key = {(s.service, s.entity_type): s for s in statuses}
        assert by_key["spotify", "likes"].local_count == 42
        assert by_key["lastfm", "likes"].local_count == 7
        # No checkpoint row for the plays channels — nothing to count against
        assert by_key["spotify", "plays"].local_count is None
        assert by_key["lastfm", "plays"].local_count is None
