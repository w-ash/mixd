"""Shared utilities for use case implementations."""

from src.application.use_cases._shared.connector_playlist_factories import (
    create_connector_playlist_items_from_tracks,
)
from src.application.use_cases._shared.connector_resolver import (
    resolve_connector,
    resolve_liked_track_connector,
    resolve_love_track_connector,
    resolve_playlist_connector,
    resolve_user_playlists_connector,
)
from src.application.use_cases._shared.playlist_resolver import (
    require_playlist_link,
    resolve_playlist,
)
from src.application.use_cases._shared.playlist_results import (
    OperationCounts,
    build_playlist_changes,
    count_operation_types,
)

__all__ = [
    "OperationCounts",
    "build_playlist_changes",
    "count_operation_types",
    "create_connector_playlist_items_from_tracks",
    "require_playlist_link",
    "resolve_connector",
    "resolve_liked_track_connector",
    "resolve_love_track_connector",
    "resolve_playlist",
    "resolve_playlist_connector",
    "resolve_user_playlists_connector",
]
