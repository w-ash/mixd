"""Composable mock builders for UnitOfWork and repository mocks.

Plain functions that construct pre-wired mocks. Tests import these instead of
re-building ``mock_uow`` fixtures from scratch in every file.

Usage::

    from tests.fixtures.mocks import make_mock_uow

    uow = make_mock_uow()  # lazy repos
    uow = make_mock_uow(track_repo=custom_mock)  # pre-configure one repo
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.domain.entities.progress import ProgressEmitter

# ---------------------------------------------------------------------------
# Individual repository mocks
# ---------------------------------------------------------------------------


def make_mock_track_repo(**overrides) -> AsyncMock:
    """Build an ``AsyncMock`` mimicking :class:`TrackRepositoryProtocol`."""
    repo = AsyncMock()
    repo.find_tracks_by_ids.return_value = overrides.pop("find_tracks_by_ids", {})
    repo.save_track.side_effect = overrides.pop("save_track", None)
    repo.save_tracks.return_value = overrides.pop("save_tracks", [])
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_playlist_repo(**overrides) -> AsyncMock:
    """Build an ``AsyncMock`` mimicking :class:`PlaylistRepositoryProtocol`."""
    repo = AsyncMock()
    repo.get_playlist_by_id.return_value = overrides.pop("get_playlist_by_id", None)
    repo.save_playlist.side_effect = overrides.pop("save_playlist", lambda p: p)
    repo.delete_playlist.return_value = overrides.pop("delete_playlist", True)
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_connector_repo(**overrides) -> AsyncMock:
    """Build an ``AsyncMock`` mimicking :class:`ConnectorRepositoryProtocol`."""
    repo = AsyncMock()
    repo.find_tracks_by_connectors.return_value = overrides.pop(
        "find_tracks_by_connectors", {}
    )
    repo.ingest_external_tracks_bulk.return_value = overrides.pop(
        "ingest_external_tracks_bulk", []
    )
    repo.map_track_to_connector.return_value = overrides.pop(
        "map_track_to_connector", None
    )
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_checkpoint_repo(**overrides) -> AsyncMock:
    """Build an ``AsyncMock`` mimicking :class:`CheckpointRepositoryProtocol`."""
    repo = AsyncMock()
    repo.get_sync_checkpoint.return_value = overrides.pop("get_sync_checkpoint", None)
    repo.save_sync_checkpoint.return_value = overrides.pop("save_sync_checkpoint", None)
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_like_repo(**overrides) -> AsyncMock:
    """Build an ``AsyncMock`` mimicking :class:`LikeRepositoryProtocol`."""
    repo = AsyncMock()
    repo.save_track_likes_batch.return_value = overrides.pop(
        "save_track_likes_batch", []
    )
    repo.get_liked_status_batch.return_value = overrides.pop(
        "get_liked_status_batch", {}
    )
    repo.get_all_liked_tracks.return_value = overrides.pop("get_all_liked_tracks", [])
    repo.count_liked_tracks.return_value = overrides.pop("count_liked_tracks", 0)
    repo.count_liked_by_service.return_value = overrides.pop(
        "count_liked_by_service", {}
    )
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_plays_repo(**overrides) -> AsyncMock:
    """Build an ``AsyncMock`` mimicking :class:`PlaysRepositoryProtocol`."""
    repo = AsyncMock()
    repo.bulk_insert_plays.return_value = overrides.pop("bulk_insert_plays", (0, 0))
    repo.get_play_aggregations.return_value = overrides.pop("get_play_aggregations", {})
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_metrics_repo(**overrides) -> AsyncMock:
    """Build an ``AsyncMock`` mimicking :class:`MetricsRepositoryProtocol`."""
    repo = AsyncMock()
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


# ---------------------------------------------------------------------------
# Progress tracking helpers
# ---------------------------------------------------------------------------


def make_tracking_emitter(uow: MagicMock) -> tuple[AsyncMock, list[str]]:
    """Build a mock ``ProgressEmitter`` that records call order with ``uow.commit``.

    Returns ``(emitter, call_order)`` where *call_order* tracks ``"commit"``
    and ``"complete"`` events — useful for asserting that ``uow.commit()``
    fires before ``complete_operation()``.
    """
    call_order: list[str] = []

    uow.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

    emitter = AsyncMock(spec=ProgressEmitter)
    emitter.start_operation = AsyncMock(return_value="op-id")
    emitter.complete_operation = AsyncMock(
        side_effect=lambda *_: call_order.append("complete")
    )
    emitter.emit_progress = AsyncMock()

    return emitter, call_order


# ---------------------------------------------------------------------------
# UnitOfWork mock
# ---------------------------------------------------------------------------


def make_mock_uow(**repo_overrides) -> MagicMock:
    """Build a mock UnitOfWork with pre-wired repository mocks.

    All repositories are eagerly created and accessible via both
    ``uow.get_*_repository()`` (call) and ``uow.get_*_repository.return_value``
    (attribute access) for compatibility with existing test patterns.

    Parameters
    ----------
    track_repo, playlist_repo, connector_repo, checkpoint_repo, like_repo,
    plays_repo, metrics_repo : AsyncMock, optional
        Pre-built repository mocks. If omitted, a default is created.
    connector_provider : MagicMock, optional
        Pre-built service connector provider.

    Returns
    -------
    MagicMock
        A mock UoW whose ``get_*_repository()`` methods return ``AsyncMock``
        repositories and whose async-context-manager protocol is wired up.
    """
    uow = MagicMock()

    uow.get_track_repository = MagicMock(
        return_value=repo_overrides.get("track_repo", make_mock_track_repo())
    )
    uow.get_playlist_repository = MagicMock(
        return_value=repo_overrides.get("playlist_repo", make_mock_playlist_repo())
    )
    uow.get_connector_repository = MagicMock(
        return_value=repo_overrides.get("connector_repo", make_mock_connector_repo())
    )
    uow.get_checkpoint_repository = MagicMock(
        return_value=repo_overrides.get("checkpoint_repo", make_mock_checkpoint_repo())
    )
    uow.get_like_repository = MagicMock(
        return_value=repo_overrides.get("like_repo", make_mock_like_repo())
    )
    uow.get_plays_repository = MagicMock(
        return_value=repo_overrides.get("plays_repo", make_mock_plays_repo())
    )
    uow.get_metrics_repository = MagicMock(
        return_value=repo_overrides.get("metrics_repo", make_mock_metrics_repo())
    )

    # Connector provider (optional override)
    uow.get_service_connector_provider = MagicMock(
        return_value=repo_overrides.get("connector_provider", MagicMock())
    )

    # Async context manager protocol
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.commit = AsyncMock()
    uow.rollback = AsyncMock()

    return uow
