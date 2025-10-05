"""Shared utilities for use case implementations."""

from src.application.use_cases._shared.connector_playlist_factories import (
    create_connector_playlist_item_from_track,
    create_connector_playlist_item_with_extras,
    create_connector_playlist_items_from_tracks,
)
from src.application.use_cases._shared.metadata_builder import (
    PlaylistMetadataBuilder,
    build_api_execution_metadata,
    build_database_update_metadata,
    build_error_metadata,
)
from src.application.use_cases._shared.playlist_results import (
    ApiExecutionResult,
    ApiMetadata,
    AppendOperationResult,
    ExternalApiResponse,
    OperationCounts,
)
from src.application.use_cases._shared.operation_counters import (
    count_operation_types,
)
from src.application.use_cases._shared.playlist_validator import (
    ConnectorPlaylistUpdateValidator,
    ConnectorPlaylistValidationResult,
    classify_connector_api_error,
    classify_database_error,
)

__all__ = [
    "ApiExecutionResult",
    "ApiMetadata",
    "AppendOperationResult",
    "ExternalApiResponse",
    "OperationCounts",
    "PlaylistMetadataBuilder",
    "build_api_execution_metadata",
    "build_database_update_metadata",
    "build_error_metadata",
    "ConnectorPlaylistUpdateValidator",
    "ConnectorPlaylistValidationResult",
    "classify_connector_api_error",
    "classify_database_error",
    "count_operation_types",
    "create_connector_playlist_item_from_track",
    "create_connector_playlist_item_with_extras",
    "create_connector_playlist_items_from_tracks",
]
