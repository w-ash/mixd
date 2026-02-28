"""Shared utilities for use case implementations."""

from src.application.use_cases._shared.connector_playlist_factories import (
    create_connector_playlist_items_from_tracks,
)
from src.application.use_cases._shared.connector_resolver import (
    resolve_connector,
    resolve_liked_track_connector,
    resolve_love_track_connector,
    resolve_playlist_connector,
)
from src.application.use_cases._shared.metadata_builder import (
    PlaylistMetadataBuilder,
    build_api_execution_metadata,
)
from src.application.use_cases._shared.operation_counters import (
    count_operation_types,
)
from src.application.use_cases._shared.playlist_resolver import resolve_playlist
from src.application.use_cases._shared.playlist_results import (
    ApiMetadata,
    AppendOperationResult,
    OperationCounts,
)
from src.application.use_cases._shared.playlist_validator import (
    classify_connector_api_error,
    classify_database_error,
)
from src.application.use_cases._shared.track_persistence import persist_unsaved_tracks

__all__ = [
    "ApiMetadata",
    "AppendOperationResult",
    "OperationCounts",
    "PlaylistMetadataBuilder",
    "build_api_execution_metadata",
    "classify_connector_api_error",
    "classify_database_error",
    "count_operation_types",
    "create_connector_playlist_items_from_tracks",
    "persist_unsaved_tracks",
    "resolve_connector",
    "resolve_liked_track_connector",
    "resolve_love_track_connector",
    "resolve_playlist",
    "resolve_playlist_connector",
]
