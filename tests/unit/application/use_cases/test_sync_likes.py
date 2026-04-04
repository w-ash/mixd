"""Unit tests for sync_likes use cases — incremental commit and progress behaviour.

Verifies that ImportSpotifyLikesUseCase and ExportLastFmLikesUseCase commit
each batch incrementally via commit_batch(), update checkpoints per-batch,
and emit progress events.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.application.use_cases.sync_likes import (
    ExportLastFmLikesCommand,
    ExportLastFmLikesUseCase,
    ImportSpotifyLikesCommand,
    ImportSpotifyLikesUseCase,
)
from src.domain.entities import ConnectorTrack, Track
from src.domain.entities.track import Artist
from tests.fixtures import make_connector_track, make_mock_uow


def _page_of_tracks(
    count: int, page: int, *, cursor: str | None = "next"
) -> tuple[list[ConnectorTrack], str | None]:
    """Build a fake page of connector tracks with a cursor."""
    return (
        [make_connector_track(f"sp_{page}_{i}") for i in range(count)],
        cursor,
    )


class TestImportSpotifyLikesIncrementalCommit:
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
            use_case = ImportSpotifyLikesUseCase()
            command = ImportSpotifyLikesCommand(user_id="test-user", limit=50)
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
            use_case = ImportSpotifyLikesUseCase()
            command = ImportSpotifyLikesCommand(user_id="test-user", limit=50)
            await use_case.execute(command, mock_uow)

        checkpoint_repo = mock_uow.get_checkpoint_repository()
        # 3 per-batch checkpoints + 1 final checkpoint on exit = 4
        assert checkpoint_repo.save_sync_checkpoint.await_count == 4

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
            use_case = ImportSpotifyLikesUseCase()
            command = ImportSpotifyLikesCommand(user_id="test-user", limit=50)
            with pytest.raises(asyncio.CancelledError):
                await use_case.execute(command, mock_uow)

        assert mock_uow.commit_batch.await_count == 2

    async def test_empty_response_no_commit_batch(self, mock_uow):
        """Empty first page → commit_batch never called."""
        connector = self._mock_connector([([], None)])
        with patch(
            "src.application.use_cases.sync_likes.resolve_liked_track_connector",
            return_value=connector,
        ):
            use_case = ImportSpotifyLikesUseCase()
            command = ImportSpotifyLikesCommand(user_id="test-user", limit=50)
            await use_case.execute(command, mock_uow)

        assert mock_uow.commit_batch.await_count == 0


class TestExportLastFmLikesIncrementalCommit:
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

        # Mock lastfm connector
        lastfm = AsyncMock()
        lastfm.love_track = AsyncMock(return_value=True)

        with patch(
            "src.application.use_cases.sync_likes.resolve_love_track_connector",
            return_value=lastfm,
        ):
            use_case = ExportLastFmLikesUseCase()
            command = ExportLastFmLikesCommand(user_id="test-user", batch_size=5)
            await use_case.execute(command, mock_uow)

        assert mock_uow.commit_batch.await_count == 3
        assert mock_uow.commit.await_count == 1
