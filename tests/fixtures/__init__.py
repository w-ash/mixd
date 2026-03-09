"""Shared test factories and mock builders.

Import from here for convenience::

    from tests.fixtures import make_track, make_mock_uow
"""

from tests.fixtures.factories import (
    make_connector_playlist,
    make_connector_playlist_item,
    make_connector_track,
    make_playlist,
    make_playlist_with_entries,
    make_spotify_track,
    make_track,
    make_tracks,
    make_workflow,
    make_workflow_def,
)
from tests.fixtures.mocks import (
    make_mock_checkpoint_repo,
    make_mock_connector_playlist_repo,
    make_mock_connector_repo,
    make_mock_like_repo,
    make_mock_metric_config,
    make_mock_metrics_repo,
    make_mock_playlist_link_repo,
    make_mock_playlist_repo,
    make_mock_plays_repo,
    make_mock_track_repo,
    make_mock_uow,
    make_mock_workflow_repo,
    make_mock_workflow_run_repo,
    make_tracking_emitter,
)

__all__ = [
    "make_connector_playlist",
    "make_connector_playlist_item",
    "make_connector_track",
    "make_mock_checkpoint_repo",
    "make_mock_connector_playlist_repo",
    "make_mock_connector_repo",
    "make_mock_like_repo",
    "make_mock_metric_config",
    "make_mock_metrics_repo",
    "make_mock_playlist_link_repo",
    "make_mock_playlist_repo",
    "make_mock_plays_repo",
    "make_mock_track_repo",
    "make_mock_uow",
    "make_mock_workflow_repo",
    "make_mock_workflow_run_repo",
    "make_playlist",
    "make_playlist_with_entries",
    "make_spotify_track",
    "make_track",
    "make_tracking_emitter",
    "make_tracks",
    "make_workflow",
    "make_workflow_def",
]
